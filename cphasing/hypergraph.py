#!/usr/bin/env python
# -*- coding:utf-8 -*-

"""
extract the hyperedges from pore-c table
"""

import logging
import gc
import msgspec
import os
import os.path as op
import sys
import shutil

import numpy as np
import pandas as pd
import polars as pl
pl.enable_string_cache()
from joblib import Parallel, delayed
from pathlib import Path
from pandarallel import pandarallel 
from pytools import natsorted
from subprocess import Popen, PIPE

from .pqs import PQS
from .algorithms.hypergraph import HyperEdges
from .utilities import (listify, 
                        list_flatten, 
                        is_compressed_table_empty, 
                        decompress_cmd)
from ._config import *

logger = logging.getLogger(__name__)


if pd.__version__.split(".")[0] == 1:
    pandas_version = 1
elif pd.__version__.split(".")[0] == 2:
    pandas_version = 2
else:
    pandas_version = 1


class Extractor:
    """
    extract edges from pairs file.

    Params:
    --------
    pairs_pathes: list
        list of pairs file
    contig_idx: dict
        dictionary of contig idx
    contigsizes: dict
        dictionary of contig sizes
    threads: int
        number of threads
    
    Examples:
    --------
    >>> extractor = Extractor(pairs_pathes, contig_idx, contigsizes)
 
    """
        
    def __init__(self, pairs_pathes, contig_idx, contigsizes, 
                 min_quality=1, hcr_bed=None, hcr_invert=False,
                 threads=4, edge_length=2e6, low_memory=True,
                 log_dir="logs"):
        self.pairs_pathes = listify(pairs_pathes)
        self.contig_idx = contig_idx
        self.contigsizes = contigsizes
        
        max_length = max(self.contigsizes.values())
        if max_length < 2**32 / 2:
            self.pos_dtype = pl.UInt32
        else:
            self.pos_dtype = pl.UInt64
    

        self.min_mapq = min_quality
        self.hcr_bed = hcr_bed
        self.hcr_invert = hcr_invert
        self.threads = threads 

        os.environ["POLARA_MAX_THREADS"] = str(threads)
        
        self.edge_length = edge_length
        self.low_memory = low_memory

        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.edges = self.generate_edges()

    @staticmethod
    def _process_df(df, contig_idx, threads=1):
        pandarallel.initialize(nb_workers=min(10, threads), verbose=0)
        df['chrom1'] = df['chrom1'].parallel_map(contig_idx.get)
        df['chrom2'] = df['chrom2'].parallel_map(contig_idx.get)
        df = df.dropna(subset=['chrom1', 'chrom2'], axis=0, how="any")

        return df

    def generate_edges(self):
        """
        """
        logger.info(f"Extract edges from pairs.") 
        if self.low_memory:
            # dtype={'chrom1': 'category', 'chrom2': 'category', 'mapq': 'int8'}
            dtype = {'chrom1': pl.Categorical, 'chrom2': pl.Categorical, 'mapq': pl.Int8}
        else:
            dtype={'mapq': pl.Int8}
        
        if self.edge_length:
            columns = ['chrom1', 'pos1', 'chrom2', 'pos2', 'mapq']
            usecols = [1, 2, 3, 4, 7]
        else:
            columns = ['chrom1', 'chrom2', 'mapq']
            usecols = [1, 3, 7]
        
        if len(self.pairs_pathes) == 1:
            pairs_prefix = Path(Path(self.pairs_pathes[0]).name)
            while pairs_prefix.suffix in {'.pairs', '.gz', '.pqs'}:
                pairs_prefix = pairs_prefix.with_suffix('')
                    

            if Path(self.pairs_pathes[0]).is_dir():
                p = PQS(path=self.pairs_pathes[0], threads=self.threads)
                
                p.init_read()
                if not p.is_pairs():
                    logger.error(f"The input `{self.pairs_pathes[0]}` is not a pairs file, please check it.")
                    sys.exit(-1)
                

                if self.hcr_bed:
                    logger.info(f"Filtering pairs by {self.hcr_bed} ...")
                    cmd = ["cphasing-rs", "pairs-intersect", 
                           self.pairs_pathes[0], self.hcr_bed, 
                           "-q", str(self.min_mapq)]

                    if self.hcr_invert:
                        cmd.append("--invert")

                    cmd.extend(["-o", f"{pairs_prefix}.intersect.pqs"])
                    cmd.append(f"2>{self.log_dir}/{pairs_prefix}.pairs.intersect.log")


                    flag = os.system(" ".join(cmd))
                    assert flag == 0, "Failed to execute command, please check log."

                    p = PQS(path=f"{pairs_prefix}.intersect.pqs", threads=self.threads)
                    p.init_read()
                
                chunks = p.read(min_mapq=self.min_mapq, return_as='files')

                res = p.to_hg_df(chunks, self.contig_idx, self.min_mapq, 
                                 edge_length=self.edge_length)
                
                if Path(f"{pairs_prefix}.intersect.pqs").exists():
                    shutil.rmtree(f"{pairs_prefix}.intersect.pqs")  
                
                res = res.with_row_count("col")
                df1 = res.select([
                    pl.col("chrom1").alias("row"),
                    pl.col("col"),
                    pl.col("mapq")
                ])
            
                df2 = res.select([
                    pl.col("chrom2").alias("row"),
                    pl.col("col"),
                    pl.col("mapq")
                ])

                res = pl.concat([df1, df2]).to_pandas()

            else:
                if is_compressed_table_empty(self.pairs_pathes[0]):
                    logger.error(f"The pairs `{self.pairs_pathes[0]}` is empty, can not load anything, please check it.")
                    sys.exit(-1)    

                input_file = ""
                if not self.hcr_bed:
                    input_file = self.pairs_pathes[0]
                else:
                    
                    if str(self.pairs_pathes[0]).endswith(".gz"):
                        cmd0 = decompress_cmd(str(self.pairs_pathes[0]), str(self.threads))
                        cmd = (f"{' '.join(cmd0)} 2>{self.log_dir}/{pairs_prefix}.decompress.hcr.log | "
                                f"cphasing-rs pairs-intersect - {self.hcr_bed} -q {self.min_mapq} -o temp.{pairs_prefix}.hcr.pairs.gz")
                    else:
                        cmd = f"cphasing-rs pairs-intersect {self.pairs_pathes[0]} {self.hcr_bed} -q {self.min_mapq} -o temp.{pairs_prefix}.hcr.pairs.gz"

                    if self.hcr_invert:
                        cmd += " --invert"
                    cmd += f" 2>{self.log_dir}/{pairs_prefix}.pairs.intersect.log"

                    logger.info(f"Generating hcr pairs by {self.hcr_bed} ...")
                    flag = os.system(cmd)
                    assert flag == 0, "Failed to execute command, please check log."
                    input_file = f"temp.{pairs_prefix}.hcr.pairs.gz"      

                p = pd.read_csv(self.pairs_pathes[0], sep='\t', comment="#", 
                                    header=None, index_col=None, nrows=1)
                if len(p.columns) >= 8  and isinstance(p[7].values[0], np.int64) and p[7].values[0] <= 60:
                        
                    p = pl.read_csv(input_file, separator='\t', has_header=False,
                                    comment_prefix="#", columns=usecols,
                                    new_columns=columns,
                                    dtypes=dtype)
                    if self.hcr_bed:
                        if Path(f"temp.{pairs_prefix}.hcr.pairs.gz").exists():
                            os.remove(f"temp.{pairs_prefix}.hcr.pairs.gz")
                    

                    if self.min_mapq > 0:
                        p = p.filter(pl.col('mapq') >= self.min_mapq)

                    
                    if self.edge_length:
                        edge_length = self.edge_length
                        p = (
                            p.with_columns(
                                [
                                    pl.col("chrom1")
                                    .map_elements(
                                        self.contigsizes.get, skip_nulls=False
                                    )
                                    .alias("length1"),
                                    pl.col("chrom2")
                                    .map_elements(
                                        self.contigsizes.get, skip_nulls=False
                                    )
                                    .alias("length2"),
                                ]
                            )
                            .filter(
                                ((pl.col("pos1") < edge_length)
                                    | (pl.col("pos1") > (pl.col("length1") - edge_length)))
                                & ((pl.col("pos2") < edge_length)
                                    | (pl.col("pos2") > (pl.col("length2") - edge_length)))
                            )
                            .select(["chrom1", "chrom2", "mapq"])
                        )

                    p = p.to_pandas()

                    res = Extractor._process_df(p, self.contig_idx, self.threads)   
                    
                    res = res.reset_index(drop=True).reset_index()
            
                    res = pd.concat([res[['chrom1', 'index', 'mapq']].rename(
                                                columns={'chrom1': 'row', 'index': 'col'}),
                                    res[['chrom2', 'index', 'mapq']].rename(
                                                columns={'chrom2': 'row', 'index': 'col'})], 
                                    axis=0)
                    
                else:

                    p = pl.read_csv(input_file, separator='\t', has_header=False,
                                    comment_prefix="#", columns=[1, 3],
                                    new_columns=['chrom1', 'chrom2'],
                                    dtypes=dtype)

                    if self.hcr_bed:
                        if Path(f"temp.{pairs_prefix}.hcr.pairs").exists():
                            os.remove(f"temp.{pairs_prefix}.hcr.pairs")
                    

                    p = p.to_pandas()

                    res = Extractor._process_df(p, self.contig_idx, self.threads)   
                    res = res.reset_index(drop=True).reset_index()
                    res = pd.concat([res[['chrom1', 'index']].rename(
                                                columns={'chrom1': 'row', 'index': 'col'}),
                                    res[['chrom2', 'index']].rename(
                                                columns={'chrom2': 'row', 'index': 'col'})], 
                                    axis=0)

        else: 
            p_list = self.pairs_pathes
            
            threads_2 = self.threads // len(p_list) + 1
            threads_1 = int(self.threads / threads_2)
            if threads_1 == 0:
                threads_1 = 1

            if not self.hcr_bed:
                def get_file(i):
                    if is_compressed_table_empty(i):
                        logger.error(f"The pairs `{i}` is empty, can not load anything, please check it.")
                        return None
                    return i
            else:
                def get_file(i):
                    cmd = f"cphasing-rs pairs-intersect {i} {self.hcr_bed} -q {self.min_mapq}"
                    if self.hcr_invert:
                        cmd += " --invert"
                    process = Popen(cmd, shell=True, stdout=PIPE, stderr=PIPE)
                    stdout, stderr = process.communicate()

                    return stdout
                


            # dtype={'chrom1': 'category', 'chrom2': 'category', 'mapq': 'int8'}
            p = pd.read_csv(self.pairs_pathes[0], sep='\t', comment="#", 
                                header=None, index_col=None, nrows=1)
            if len(p.columns) >= 8  and isinstance(p[7].values[0], np.int64) and p[7].values[0] <= 60:
                # res = Parallel(n_jobs=min(self.threads, len(p_list)))(delayed(
                #                 lambda x: pd.read_csv(get_file(x), sep='\t', comment="#",
                #                                 header=None, index_col=None, 
                #                                 # compression=compression,
                #                                 dtype=dtype,
                #                                 usecols=[1, 3, 7], names=['chrom1', 'chrom2', 'mapq'],
                #                                 ).query(f'mapq >= {self.min_mapq}'))
                #                                 (i) for i in p_list)
                def read_csv(x):
                    df = pl.read_csv(x, separator='\t', has_header=False, 
                                     comment_prefix="#", columns=[1, 3, 7],
                                    new_columns=['chrom1', 'chrom2', 'mapq'],
                                    dtypes=dtype)
                    if self.min_mapq > 0:
                        df = df.filter(pl.col('mapq') >= self.min_mapq)

                    return df.to_pandas()
                with Parallel(backend="loky", n_jobs=min(self.threads, len(p_list))) as parallel:   
                    res = parallel(n_jobs=min(self.threads, len(p_list)))(delayed(
                                lambda x: read_csv(get_file(x)))(i) for i in p_list)

            else:
                def read_csv(x):
                    df = pl.read_csv(x, separator='\t', has_header=False, 
                                     comment_prefix="#", columns=[1, 3],
                                    new_columns=['chrom1', 'chrom2'],
                                    dtypes=dtype)
    
                    return df.to_pandas()
                
                res = Parallel(n_jobs=min(self.threads, len(p_list)))(delayed(
                                lambda x: read_csv(get_file(x)))(i) for i in p_list)
                
            args = [ (i, self.contig_idx, threads_2) for i in res ]
        

            with Parallel(backend="loky", n_jobs=threads_1) as parallel:
                res = parallel(n_jobs=threads_1)(delayed(
                                Extractor._process_df)(i, j, k) for i, j, k in args)
            
            if len(p.columns) >= 8  and isinstance(p[7].values[0], np.int64) and p[7].values[0] <= 60:
                res = pd.concat(res, axis=0).reset_index(drop=True).reset_index()
                res = pd.concat([res[['chrom1', 'index', 'mapq']].rename(
                                            columns={'chrom1': 'row', 'index': 'col'}),
                                res[['chrom2', 'index', 'mapq']].rename(
                                            columns={'chrom2': 'row', 'index': 'col'})], 
                                axis=0)
            else:
                res = pd.concat(res, axis=0).reset_index(drop=True).reset_index()
                res = pd.concat([res[['chrom1', 'index']].rename(
                                            columns={'chrom1': 'row', 'index': 'col'}),
                                res[['chrom2', 'index']].rename(
                                            columns={'chrom2': 'row', 'index': 'col'})], 
                                axis=0)
                
            
        number_of_contigs = len(self.contig_idx)
        length = res['col'].max()
        logger.info(f"Result of {length:,} raw "
                    f"edges of {number_of_contigs:,} contigs. "
                    "Note: it's not the final statistics for hypergraph.")
        
        logger.debug("Generating hyperedges ...")
        if 'mapq' in res.columns:
            return HyperEdges(idx=self.contig_idx, 
                            row=res['row'].values.tolist(), 
                            col=res['col'].values.tolist(),
                            # count=np.ones(len(res['col']), dtype=np.uint32).tolist(),
                            contigsizes=self.contigsizes,
                            mapq=res['mapq'].values.tolist())

        else:
            return HyperEdges(idx=self.contig_idx, 
                            row=res['row'].values.tolist(), 
                            col=res['col'].values.tolist(),
                            # count=np.ones(len(res['col']), dtype=np.uint32).tolist(),
                            contigsizes=self.contigsizes,
                            mapq=[])

    def save(self, output):
        with open(output, 'wb') as out:
            out.write(msgspec.msgpack.encode(self.edges))
        
        logger.info(f"Successful output graph into `{output}`")
    
class ExtractorSplit:
    """
    extract split edges from pairs file.

    Params:
    --------
    pairs_pathes: list
        list of pairs file
    contig_idx: dict
        dictionary of contig idx
    contigsizes: dict
        dictionary of contig sizes
    threads: int
        number of threads
    
    Examples:
    --------
    >>> extractor = ExtractorSplit(pairs_pathes, contig_idx, contigsizes)
 
    """
    def __init__(self, 
                 pairs_pathes, 
                 contig_idx, 
                 contigsizes, 
                 split=2,
                 min_quality=1,
                 threads=4):
        self.pairs_pathes = listify(pairs_pathes)
        self.contig_idx = contig_idx
        self.contigsizes = contigsizes
        self.min_mapq = min_quality
        self.threads = threads 
        
        self.split = split
        self.split_contig_idx = {}
        idx = 0
        for contig in self.contig_idx:
            for i in range(split):
                self.split_contig_idx[f"{contig}_{i}"] = idx
                idx += 1 
        
        self.edges = self.generate_edges()

    @property
    def split_contigsizes(self):
        split_contigsizes = {}
        for contig in self.split_contig_idx:
            split_contigsizes[contig] = self.contigsizes[contig.rsplit("_", 1)[0]] // self.split
        
        return split_contigsizes

    @staticmethod
    def _process_df(df, contig_sizes, split_contig_idx, split=2, threads=1):
        pandarallel.initialize(nb_workers=min(10, threads), verbose=0)
        # df['chrom1'] = df['chrom1'].parallel_map(contig_idx.get)
        # df['chrom2'] = df['chrom2'].parallel_map(contig_idx.get)
        # df = df.dropna(subset=['chrom1', 'chrom2'], axis=0, how="any")

        df['contigsize'] = df['chrom1'].parallel_map(contig_sizes)
        df['pos1'] = df['pos1'] // (df['contigsize'] // split)
        df['contigsize'] = df['chrom2'].parallel_map(contig_sizes)
        df['pos2'] = df['pos2'] // (df['contigsize'] // split)
        
        df['pos1'] = df['pos1'].astype(str) 
        df['pos2'] = df['pos2'].astype(str) 
        df.dropna(axis=0, how='any', inplace=True)

        df['chrom1'] = df['chrom1'].str.cat(df['pos1'], sep="_")
        df['chrom2'] = df['chrom2'].str.cat(df['pos2'], sep="_")
        df['chrom1'] = df['chrom1'].parallel_map(split_contig_idx.get)
        df['chrom2'] = df['chrom2'].parallel_map(split_contig_idx.get)
        
        return df

    def generate_edges(self):
        """
        """
        logger.info(f"Extract edges from pairs.") 

        if len(self.pairs_pathes) == 1:
            # if self.pairs_pathes[0][-3:] == ".gz":
            #     compression = 'gzip'
            # else:
            #     compression='infer'
            p = pd.read_csv(self.pairs_pathes[0], sep='\t', comment="#",
                                header=None, index_col=None,
                                usecols=[1, 2, 3, 4], 
                                names=['chrom1', 'pos1', 'chrom2', 'pos2'], 
                               )

            res = ExtractorSplit._process_df(p, self.contigsizes, 
                                                self.split_contig_idx, 
                                                self.split, self.threads)    
            res = res.reset_index()
  
            res = pd.concat([res[['chrom1', 'index']].rename(
                                        columns={'chrom1': 'row', 'index': 'col'}),
                              res[['chrom2', 'index']].rename(
                                        columns={'chrom2': 'row', 'index': 'col'})], 
                              axis=1)


        else: 
            p_list = self.pairs_pathes
            # if p_list[0][-3:] == ".gz":
            #     compression = 'gzip'
            # else:
            #     compression='infer'
            
            threads_2 = self.threads // len(p_list) + 1
            threads_1 = int(self.threads / threads_2)
            if threads_1 == 0:
                threads_1 = 1
            
            res = Parallel(n_jobs=min(self.threads, len(p_list)))(delayed(
                lambda x: pd.read_csv(x, sep='\t', comment="#",
                                header=None, index_col=None, 
                                # compression=compression,
                                 usecols=[1, 2, 3, 4], 
                                names=['chrom1', 'pos1', 'chrom2', 'pos2']))
                                (i) for i in p_list)
            
            args = [ (i, self.contigsizes, self.split_contig_idx, self.split, threads_2) for i in res ]
        

            res = Parallel(n_jobs=threads_1)(delayed(
                                Extractor._process_df)(i, j, k) for i, j, k in args)
            
            res = pd.concat(res, axis=1).reset_index()
            res = pd.concat([res[['chrom1', 'index']].rename(
                                        columns={'chrom1': 'row', 'index': 'col'}),
                              res[['chrom2', 'index']].rename(
                                        columns={'chrom2': 'row', 'index': 'col'})], 
                              axis=1)
        
        number_of_contigs = len(self.contig_idx)
        length = res['row'].shape[0] * res['row'].shape[1]
        logger.info(f"Result of {length:,} raw "
                    f"hyperedges of {number_of_contigs:,} contigs. "
                    "Note: it's not the final statistics for hypergraph.")
        return HyperEdges(idx=self.split_contig_idx, 
                            row=res['row'].values.flatten().tolist(), 
                            col=res['col'].values.flatten().tolist(),
                            contigsizes=self.split_contigsizes,
                            mapq=[])

    def save(self, output):
        with open(output, 'wb') as out:
            out.write(msgspec.msgpack.encode(self.edges))
        
        logger.info(f"Successful output graph into `{output}`")


def process_pore_c_table(df, contig_idx, contigsizes, threads=1,
                            min_order=2, max_order=50, 
                            min_alignments=50, is_parquet=False,
                            edge_length=2e6 
                          ):
    
    df = df.with_columns(pl.col('chrom').map_elements(contig_idx.get).alias('chrom_idx')).drop_nulls()
    df = df.with_columns(pl.col('chrom_idx').cast(pl.UInt32))
    if edge_length:
        df = df.with_columns(pl.col('chrom').map_elements(contigsizes.get, skip_nulls=False).alias('length')).drop_nulls()
        df = df.with_columns(pl.col('length').cast(pl.UInt32),
                             (pl.col('start') + (pl.col('end') - pl.col('start'))//2).alias('pos')
                             )
        df = df.filter((pl.col('pos') < edge_length) | ((pl.col('length') - pl.col('pos')) < edge_length))
        
    # df = df.with_columns(pl.col('read_idx').cast(pl.UInt32))
    
    df = df.select(['read_idx', 'chrom_idx', 'mapping_quality'])
    df_grouped_nunique = df.group_by(['read_idx']).agg(pl.col('chrom_idx').n_unique().alias('chrom_idx_nunique'))
    df = df.join(df_grouped_nunique, on='read_idx')

    df = df.filter((pl.col('chrom_idx_nunique') >= min_order) & (pl.col('chrom_idx_nunique') <= max_order))

    # df_grouped_count = df.group_by(['read_idx', 'chrom_idx']).agg(pl.col('chrom_idx').count().alias('chrom_idx_count'))
    # df = df.join(df_grouped_count, on=['read_idx', 'chrom_idx'])
    df = df.select(['read_idx', 'chrom_idx', 'mapping_quality', 
                    # 'chrom_idx_count'
                    ])
    
    df = df.unique(['read_idx', 'chrom_idx'], maintain_order=True)
    # df_grouped_read_idx_count = df.group_by(['read_idx'], maintain_order=True).count()
    # print(df_grouped_read_idx_count.with_columns(((pl.col('count') * (pl.col('count') - 1)) // 2 ).alias('contact'))['contact'].to_numpy().sum())

    # df = df.with_columns((pl.col('chrom_idx_count') * (pl.col('chrom_idx_count') - 1) // 2).alias('chrom_idx_count'))
    df = df.with_columns(pl.col('read_idx').cast(pl.Utf8).cast(pl.Categorical).to_physical())

    df = df.to_pandas()
    
    return df


class HyperExtractor:
    """
    Params:
        --------
    pore_c_table_pathes: list
        pore_c table, at least have four columns: read_id x, chrom, start, end.
    contig_idx: 
    min_order: int, default 2
        minimum contig order of pore-c reads
    max_order: int, default 50
        maximum contig order of pore-c reads
    min_alignments: int, default 30
        minimum length of alignments
    threads: int, default 10
        number of threads

    """
    HEADER = ["read_idx", "read_length", 
              "read_start", "read_end",  
              "strand", "chrom", "start",
              "end", "mapping_quality", "identity", 
              "filter_reason"]
    def __init__(self, pore_c_table_pathes, 
                            contig_idx,
                            contigsizes,
                            min_order=2, 
                            max_order=50, 
                            min_alignments=30,
                            min_quality=1,
                            edge_length=2e6,
                            hcr_bed=None,
                            hcr_invert=False,
                            threads=4,
                            is_parquet=False,
                            log_dir="logs"):
        
        self.pore_c_table_pathes = listify(pore_c_table_pathes)
        self.contig_idx = contig_idx
        self.contigsizes = contigsizes
        self.min_order = min_order
        self.max_order = max_order
        self.min_alignments = min_alignments
        self.min_quality = min_quality
        self.edge_length = edge_length
        self.hcr_bed = hcr_bed
        self.hcr_invert = hcr_invert
        self.threads = threads
        os.environ["POLARA_MAX_THREADS"] = str(threads)

        self.is_parquet = is_parquet

        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        self.pore_c_tables = self.import_pore_c_table()
        self.edges = self.generate_edges()

        
    def import_pore_c_table(self):
        """
        import pore-c table from pore
        """
        logger.info("Loading Pore-C table ...")
        pl.enable_string_cache()
        if self.edge_length:
            columns = ['read_idx', 'chrom', 'start', 'end', 'mapping_quality']
            schema = {'read_idx': pl.UInt32, 
                        'start': pl.UInt32,
                        'end': pl.UInt32,
                      'mapping_quality': pl.Int8, 
                      'chrom': pl.Categorical}
            usecols = [0, 5, 6, 7, 8]
        else:
            columns = ['read_idx', 'chrom', 'mapping_quality']
            schema = {'read_idx': pl.UInt32, 'mapping_quality': pl.Int8, 'chrom': pl.Categorical}
            usecols = [0, 5, 8]

        if len(self.pore_c_table_pathes) == 1:
            if self.hcr_bed is None:
                infile = self.pore_c_table_pathes[0]
            else:
                porec_prefix = str(Path(self.pore_c_table_pathes[0]).name).replace(".gz", "").rsplit(".", 1)[0]
                if str(self.pore_c_table_pathes[0]).endswith(".gz"):
                    cmd0 = decompress_cmd(str(self.pore_c_table_pathes[0]), str(self.threads))
                    cmd = f"{' '.join(cmd0)} 2>{self.log_dir}/{porec_prefix}.decompress.hcr.log | cphasing-rs porec-intersect - {self.hcr_bed} -o temp.{porec_prefix}.hcr.porec"
                else:
                    cmd = f"cphasing-rs porec-intersect {self.pore_c_table_pathes[0]} {self.hcr_bed} -o temp.{porec_prefix}.hcr.porec"
                if self.hcr_invert:
                    cmd += " --invert"

                cmd += f" 2>{self.log_dir}/{porec_prefix}.porec.intersect.log"
                logger.info(f"Generating hcr porec table by {self.hcr_bed} ...")
                flag = os.system(cmd)
                assert flag == 0, "Failed to execute command, please check log."

                infile = f"temp.{porec_prefix}.hcr.porec"

            if self.is_parquet:
                df = pd.read_parquet(infile, 
                                        columns=['read_idx', 'chrom',
                                                # 'start', 'end', 
                                                'mapping_quality',
                                                #'filter_reason',
                                                ],
                                    engine=PQ_ENGINE)
                
            else: 
                logger.debug("Start to load one porec table.")
                try:
                    df = pl.read_csv(infile, separator='\t', has_header=False,
                                    columns=usecols,
                                    dtypes=schema,
                                    new_columns=columns)
                except pl.exceptions.NoDataError:
                    logger.error(f"The pore-c table `{infile}` is empty, can not load anything, please check it.")
                    sys.exit(-1)
                except IsADirectoryError:
                    logger.error(f"The pore-c table `{infile}` is a directory, may be you want to load pairs.pqs file please specified `--pairs` parameter.")
                    sys.exit(-1)
                # except pl.exceptions.OutOfBoundsError:
                #     logger.error(f"The pore-c table `{infile}` is incorrect, it's not a pore-c table, "
                #                  f"may be it is a pairs, if that you should change `-pct` to `-prs`.")
                    # sys.exit(-1)
                
            if self.hcr_bed:
                if Path(f"temp.{porec_prefix}.hcr.porec").exists():
                    os.remove(f"temp.{porec_prefix}.hcr.porec")

            if self.min_quality > 0:
                df = df.filter(pl.col('mapping_quality') >= self.min_quality)
            
           
            df_list = [df]
            logger.debug("Successful load porec table.")

        else:
            infiles = []
            for i in self.pore_c_table_pathes:
                infiles.append(i)

            if not self.hcr_bed:
                def get_file(x):
                    return x 
            else:
                def get_file(x):
                    porec_prefix = str(Path(x).name).replace(".gz", "").rsplit(".", 1)[0]
                    cmd = f"cphasing-rs porec-intersect {x} {self.hcr_bed} -o temp.{porec_prefix}.hcr.porec"
                    if self.hcr_invert:
                        cmd += " --invert"
                    process = Popen(cmd, shell=True, stdout=PIPE, stderr=PIPE)
                    stdout, stderr = process.communicate()

                    return f"temp.{porec_prefix}.hcr.porec"

            if self.is_parquet:
                df_list = list(map(lambda x: pd.read_parquet(
                                x, columns=['read_idx', 'chrom', 
                                            # 'start', 'end', 
                                            'mapping_quality',
                                            #'filter_reason'
                                            ], 
                                    engine=PQ_ENGINE,),
                                    infiles))
                
                # df_list = Parallel(n_jobs=self.threads)(delayed(
                #                     lambda x: x.query("filter_reason == 'pass'")
                #                                 .drop("filter_reason", axis=1)
                #                                 )(i) for i in df_list)

                
            else:
                # if pandas_version == 2:
                #     df_list = list(map(lambda x: pd.read_csv(
                #                     x,
                #                         # usecols=['read_idx', 'chrom', 
                #                         #         # 'start', 'end', 
                #                         #         'mapping_quality',
                #                         #         #'filter_reason'
                #                         #         ], 
                #                         usecols=[0, 5, 8],
                #                         sep='\t',
                #                         index_col=None,
                #                         header=None,
                #                         engine=CSV_ENGINE,
                #                         dtype_backend=CSV_ENGINE,
                #                         ),
                #                         #filters=[('pass_filter', '=', True)]),
                #                         infiles))
                # else:
                #         df_list = list(map(lambda x: pd.read_csv(
                #                     x,
                #                         usecols=[0, 5, 8],
                #                         sep='\t',
                #                         index_col=None,
                #                         header=None,
                #                         # engine=CSV_ENGINE,
                #                         ),
                #                         infiles))
                # for df in df_list:
                #     df.columns = ['read_idx', 'chrom', 'mapping_quality']
                
                df_list = list(map(lambda x: pl.read_csv(
                    get_file(x), separator='\t', has_header=False,
                    columns=usecols, dtypes={'mapping_quality': pl.Int8, 'chrom': pl.Categorical},
                    new_columns=columns,
                ), infiles))
                
                if self.min_quality > 0:

                    df_list = Parallel(n_jobs=self.threads)(delayed(
                        lambda x: x.filter(pl.col('mapping_quality') >= self.min_quality))(i) for i in df_list)
                
                if self.edge_length:
                    df_list = Parallel(n_jobs=self.threads)(delayed(
                        lambda x: x.with_columns([
                            pl.col('chrom').map_elements(self.contigsizes.get).alias('length'),
                        ]).filter(
                            (pl.col('start') < self.edge_length) | (pl.col('end') > pl.col('length') - self.edge_length)
                        ).select(['read_idx', 'chrom', 'mapping_quality'])
                    )(i) for i in df_list)
               
        return df_list
    

    def generate_edges(self):
        """
        generate hypergraph incidence matrix


        Returns:
        --------
        H: csc_matrix
            incidence matrix for hypergraph
        vertices: list
            list of contig names

        Examples:
        --------
        >>> H, vertices = generate_hypergraph("pore_c.pq")
        """
        logger.info("Processing Pore-C table ...")
        logger.info(f"Only retained Pore-C concatemer that: \n"
                        # f"\talignment length >= {self.min_alignments} and \n"
                        # f"\t{self.min_order} <= contig order <= {self.max_order} and  \n"
                        f"\tmapping_quality >= {self.min_quality}")

        threads_2 = self.threads // len(self.pore_c_tables) + 1
        threads_1 = int(self.threads / threads_2)
        if threads_1 == 0:
            threads_1 = 1
        args = []

        logger.debug("Processing Pore-C table ...")
        if len(self.pore_c_tables) > 1:
            for i, pore_c_table in enumerate(self.pore_c_tables):
                args.append((pore_c_table, self.contig_idx, 
                             self.contigsizes, threads_2, 
                            self.min_order, self.max_order, 
                            self.min_alignments, self.is_parquet, 
                            self.edge_length))
           
            res = Parallel(n_jobs=threads_1)(
                            delayed(process_pore_c_table)(*a) for a in args)
        else:
            res = [process_pore_c_table(self.pore_c_tables[0], self.contig_idx, 
                                        self.contigsizes, threads_2, 
                                        self.min_order, self.max_order, 
                                        self.min_alignments, self.is_parquet,
                                        self.edge_length)]
        idx = 0
        mapping_quality_res = []
        
        if len(res) > 1:
            for i, df in enumerate(res):
                mapping_quality_res.append(df['mapping_quality'])
                if idx:
                    df['read_idx'] = df['read_idx'] + idx 
                    res[i] = df

                idx += len(df)
            
            res_df = pd.concat(res)
            mapping_quality_res = pd.concat(mapping_quality_res)
        else:
            res_df = res[0]
            mapping_quality_res = res_df['mapping_quality']
            
        if len(res_df) < 1:
            raise ValueError("No pore-c reads are retained, please check the pore-c table.")

        
        edges = HyperEdges(idx=self.contig_idx, 
                       row=res_df['chrom_idx'].values.flatten().tolist(),
                       col=res_df['read_idx'].values.flatten().tolist(),
                    #    count=res_df['chrom_idx_count'].values.flatten().tolist(),
                       mapq=mapping_quality_res.to_numpy().flatten().tolist(),
                       contigsizes=self.contigsizes)
        
        
        number_of_contigs = len(self.contig_idx)
        number_of_hyperedges = res_df['read_idx'].max()
        logger.info(f"Result of {number_of_hyperedges:,} raw "
                    f"hyperedges of {number_of_contigs:,} contigs. "
                    "Note: it's not the final statistics for hypergraph.")

        return edges
    
    def save(self, output):
        with open(output, 'wb') as out:
            out.write(msgspec.msgpack.encode(self.edges))

        logger.info(f"Successful output hypergraph into `{output}`")


class HyperExtractorSplit:
    HEADER = ["read_idx", "read_length", 
              "read_start", "read_end",  
              "strand", "chrom", "start",
              "end", "mapping_quality", "identity", 
              "filter_reason"]
    def __init__(self, pore_c_table_pathes, contig_idx, contigsizes, 
                 split=5,
                 min_quality=1,
                 threads=4, 
                is_parquet=False, ):
        self.pore_c_table_pathes = listify(pore_c_table_pathes)
        self.contig_idx = contig_idx
        self.contigsizes = contigsizes 
        self.min_quality = min_quality
        self.threads = threads 
        self.is_parquet = is_parquet

        self.split = split
        self.split_contig_idx = {}
        idx = 0
        for contig in self.contig_idx:
            for i in range(split):
                self.split_contig_idx[f"{contig}_{i}"] = idx
                idx += 1 

        self.pore_c_tables = self.import_pore_c_table()
        self.edges = self.generate_edges()

    @property
    def split_contigsizes(self):
        split_contigsizes = {}
        for contig in self.split_contig_idx:
            split_contigsizes[contig] = self.contigsizes[contig.rsplit("_", 1)[0]] // self.split
        
        return split_contigsizes

    @staticmethod
    def process_pore_c_table(df, contig_idx, contig_sizes, 
                             split_contig_idx,
                             split=5, threads=4, 
                             ):
        pandarallel.initialize(nb_workers=threads, verbose=0)
        df = df.set_index('chrom')
        df = df.reindex(list(contig_idx.keys()))
        df = df.reset_index().set_index('read_idx')
        df['contigsize'] = df['chrom'].parallel_map(contig_sizes.get)

        df['pos'] = ((df['start'] + df['end']) // 2) // (df['contigsize'] // split)
        df['pos'] = df['pos'].astype(str)
        
        df['chrom'] = df['chrom'].str.cat(df['pos'], sep="_")
        df['chrom_idx'] = df['chrom'].parallel_map(split_contig_idx.get)
        df = df[['chrom_idx', 'mapping_quality', 'chrom']]
        # df_grouped = df.groupby('read_idx')['chrom_idx']
        # df_grouped_nunique = df_grouped.nunique()
        # df = df.loc[(df_grouped_nunique >= min_order)]
        #             & (df_grouped_nunique <= max_order)]
    
        df = df[['chrom_idx', 'mapping_quality', 'chrom']].reset_index().drop_duplicates(['read_idx', 'chrom_idx'])
        df['read_idx'] = df['read_idx'].astype('category')
        df['read_idx'] = df['read_idx'].cat.codes

        return df         


    def import_pore_c_table(self):
        if len(self.pore_c_table_pathes) == 1:
            if Path(self.pore_c_table_pathes[0]).is_symlink():
                infile = os.readlink(self.pore_c_table_pathes[0])
            else:
                infile = self.pore_c_table_pathes[0]
        
            if self.is_parquet:
                df = pd.read_parquet(infile, 
                                        columns=['read_idx', 'chrom',
                                                'start', 'end', 
                                                'mapping_quality',
                                                #'filter_reason',
                                                ],
                                    engine=PQ_ENGINE) 
            else:
                if pandas_version == 2:
                    df = pd.read_csv(infile, 
                                sep='\t',
                                header=None,
                                index_col=None,
                                usecols=['read_idx', 'chrom',
                                            'start', 'end', 
                                            'mapping_quality',
                                            #'filter_reason'
                                            ],
                                engine=CSV_ENGINE,
                                dtype_backend=CSV_ENGINE,
                                names=self.HEADER)
                else:
                    df = pd.read_csv(infile, 
                                sep='\t',
                                header=None,
                                index_col=None,
                                usecols=['read_idx', 'chrom',
                                            'start', 'end', 
                                            'mapping_quality',
                                            #'filter_reason'
                                            ],
                                engine=CSV_ENGINE,
                                names=self.HEADER)
                
                if self.min_quality > 0:
                    df = df.query(f"mapping_quality >= {self.min_quality}")
                df_list = [df]

        

        return df_list
    
    def generate_edges(self):
        logger.info("Processing Pore-C table ...")
    
        threads_2 = self.threads // len(self.pore_c_tables) + 1
        threads_2 = min(10, threads_2)
        threads_1 = int(self.threads / threads_2)
        if threads_1 == 0:
            threads_1 = 1
        args = []
        for i, pore_c_table in enumerate(self.pore_c_tables):
            args.append((pore_c_table, self.contig_idx, self.contigsizes, self.split_contig_idx,
                         self.split, threads_2))
        
        res = Parallel(n_jobs=threads_1)(
            delayed(HyperExtractorSplit.process_pore_c_table)(i, j, k, l, m, n)
                for i, j, k, l, m, n in args
        )

        idx = 0
        mapping_quality_res = []
        for i, df in enumerate(res):
            mapping_quality_res.append(df['mapping_quality'])
            df['read_idx'] = df['read_idx'] + idx 
            res[i] = df
            idx += len(df)
        
        res_df = pd.concat(res)
        mapping_quality_res = pd.concat(mapping_quality_res)


        edges = HyperEdges(idx=self.split_contig_idx, 
                       row=res_df['chrom_idx'].values.flatten().tolist(),
                       col=res_df['read_idx'].values.flatten().tolist(),
                       mapq=mapping_quality_res.values.flatten().tolist(),
                       contigsizes=self.split_contigsizes)
        

        return edges 

    def save(self, output):
        with open(output, 'wb') as out:
            out.write(msgspec.msgpack.encode(self.edges))

        logger.info(f"Successful output hypergraph into `{output}`")        