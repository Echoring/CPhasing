#!/usr/bin/env python
# -*- coding:utf-8 -*-

"""
mapper of Hi-C data.
"""

import logging
import os
import os.path as op
import sys

import pandas as pd 

from pathlib import Path
from shutil import which
from subprocess import Popen, PIPE

from .utilities import (
    is_compressed_table_empty,
    is_empty,
    run_cmd,
    ligation_site,
    to_humanized
)

logger = logging.getLogger(__name__)
class HisatMapper(object):
    """
    single ends mapping by hisat2

    Params:
    --------

    Returns:
    --------

    Examples:
    --------
    >>> mapper = HisatMapper('reference.fasta', 'sample_R1.fastq.gz')
    """
    def __init__(self, index, fastq, enzyme, 
                    min_quality=10, 
                    threads=4, 
                    additional_arguments=(),
                    # additional_arguments=("-B" "5" "-O" "2" "-E" "1"),
                    hisat2_path='hisat2', 
                    log_dir='logs'):

        self.index = index
        self.fastq = Path(fastq)
        self.enzyme = enzyme
        self.ligation_site = ligation_site(self.enzyme)[0]
        self.threads = threads
        self.min_quality = min_quality
        self.additional_arguments = additional_arguments
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self._path = hisat2_path
        if which(self._path) is None:
            raise ValueError(f"{self._path}: command not found.")

        self.prefix = self.fastq.with_suffix('')
        while self.prefix.suffix in {'.fastq', 'gz', 'fq', '.fq', '.gz'}:
            self.prefix = self.prefix.with_suffix('')

        self.global_bam = Path(f'{self.prefix}.global.bam')
        self.unmap_fastq = Path(f'{self.prefix}.unmap.fastq')
        self.trimmed_fastq = Path(f'{self.prefix}.trimed.fastq')
        self.local_bam = Path(f'{self.prefix}.local.bam')
        self.merge_bam = Path(f'{self.prefix}.merge.bam')
        self.sorted_bam = Path(f'{self.prefix}.sorted.bam')

    def global_mapping(self):
       

        map_command = [f'{self._path}', '-x', self.index, '-U', str(self.fastq), 
                    '-k', '1', '--no-spliced-alignment', 
                    '--un', str(self.unmap_fastq),
                    '--no-softclip', '--threads', str(self.threads)]

        bam_command = ['samtools', 'view', '-bS', '-@', '4', '-F', '4', '-']
        
        logger.info('Running command:')
        logger.info('\t' + ' '.join(map_command) + ' | ' + ' '.join(bam_command)
                    + ' > ' + str(self.global_bam))

        pipelines = []
        try:
            pipelines.append(
                Popen(map_command, stdout=PIPE, 
                        stderr=open(f'{self.log_dir}/{self.prefix}'
                        '.global_mapping.log','w'), 
                        bufsize=-1)
            )

            pipelines.append(
                Popen(bam_command, stdin=pipelines[-1].stdout,
                        stdout=open(self.global_bam, 'wb'), 
                        stderr=open(f'{self.log_dir}/{self.prefix}'
                        '.global_mapping.log','w'),
                        bufsize=-1)
            )

            pipelines[-1].wait()
        
        finally:
            for p in pipelines:
                if p.poll() is None:
                    p.terminate()

    def trim_fastq(self):
        
        # command = ['../bin/cutsite_trimming', '--fastq', fastq, '--cutsite', 
        #             cutsite, '--out', self.trimed_fastq]
        
        # run_cmd(command)

        from .hic.cutsite import cutsite_trimming
        cutsite_trimming(self.unmap_fastq, self.ligation_site, self.trimmed_fastq)

    def trimmed_mapping(self):
        
        map_command = [self._path, '-x', self.index, '-U', str(self.trimmed_fastq), 
                    '-k', '1', '--no-spliced-alignment', '--no-softclip',
                    '--threads', str(self.threads)]
        bam_command = ['samtools', 'view', '-@', '4', '-bS', '-']

        logger.info('Running command:')
        logger.info('\t' + ' '.join(map_command) + ' | ' + ' '.join(bam_command)
                    + ' > ' + str(self.local_bam))

        pipelines = []
        try:
            pipelines.append(
                Popen(map_command, stdout=PIPE, 
                        stderr=open(f'{self.log_dir}/{self.prefix}'
                        '.trimmed_mapping.log','w'), 
                        bufsize=-1)
            )

            pipelines.append(
                Popen(bam_command, stdin=pipelines[-1].stdout,
                        stdout=open(self.local_bam, 'wb'), 
                        stderr=open(f'{self.log_dir}/{self.prefix}'
                        '.global_mapping.log','w'),
                        bufsize=-1)
            )

            pipelines[-1].wait()
        
        finally:
            for p in pipelines:
                if p.poll() is None:
                    p.terminate()


    def combine(self):
        command = ['samtools', 'merge', '-f', '-@', str(self.threads), 
                    str(self.merge_bam), str(self.global_bam), str(self.local_bam)]
        

        run_cmd(command, log=f'{self.log_dir}/{self.prefix}.bam_merge.log')

    def sort(self):
        command = ['samtools', 'sort', '-@', str(self.threads), 
                    '-n', str(self.merge_bam), '-o', str(self.sorted_bam)]
        
        
        run_cmd(command, log=f'{self.log_dir}/{self.prefix}.bam_sort.log')

    def clean(self):
        self.global_bam.unlink()
        self.trimmed_fastq.unlink()
        self.unmap_fastq.unlink()
        self.local_bam.unlink()
        self.merge_bam.unlink()

    def run(self):
        self.global_mapping()
        self.trim_fastq()
        self.trimmed_mapping()
        self.combine()
        self.sort()

    @classmethod
    def pair():
        command = []

    @classmethod
    def create_index(self, reference):
        cmd = [f'{self._path}-build', '-p', str(self.threads),
                str(self.index.parent), str(self.index)]
        
        run_cmd(cmd)

class ChromapMapper:
    """
    Mapper for Hi-C reads using chromap.

    Params:
    --------
    reference: str
        contig-level assembly.
    read1: str
        Hi-C paired-end read1.
    read2: str
        Hi-C paired-end read2.
    min_quality: int, default 1
        minimum number of mapping quality
    threads: int, default 4
        number of threads.
    additional_arguments: tuple, default None
        additional arguments of chromap.
    path: str, default "chromap"
        Path of chromap.
    log_dir: str, default "logs"
        directory of logs.
    
    Returns:
    --------
    object:
        object of ChromapMapper
    
    Examples:
    --------
    >>> cm = ChromapMapper(reference, read1, read2, 
                min_quality=mapq, threads=threads)
    >>> cm.run()
    """
    def __init__(self, reference, read1, read2, 
                    kmer_size=17, window_size=7, min_quality=30, 
                    threads=4, additional_arguments=(), 
                    path='_chromap', log_dir='logs'):
        

        self.reference = Path(reference)
        self.index_path = Path(f'{self.reference.stem}.index')
        self.contigsizes = Path(f'{self.reference.stem}.contigsizes')
        self.read1 = Path(read1)
        self.read2 = Path(read2)
        self.threads = threads
        self.kmer_size = kmer_size
        self.window_size = window_size
        self.min_quality = min_quality
        self.additional_artuments=()
        
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self._path = path
        if which(self._path) is None:
            if which(self._path.rstrip("_")) is None:
                raise ValueError(f"{self._path}: command not found")
            else:
                self._path = self._path.rstrip("_")

        if which('crabz') is None and which('pigz') is None:
            raise ValueError(f"pigz: command not found")
    
        if which('crabz') is None:
            self.compressor = 'pigz'
        else:
            self.compressor = 'crabz'

        self.prefix = Path(self.read1.stem).with_suffix('')
        while self.prefix.suffix in {'.fastq', 'gz', 'fq', '.fq', '.gz', '_R1', '_1', '_2', '.fasta', 'fasta', '.fa'}:
            self.prefix = self.prefix.with_suffix('')
        
        if str(self.prefix).endswith("_R1"):
            self.prefix = Path(str(self.prefix)[:-3])
        elif str(self.prefix).endswith("_1"):
            self.prefix = Path(str(self.prefix)[:-2])

        self.prefix = Path(str(self.prefix).replace('_R1', '').replace('_1', ''))

        self.output_pairs = Path(f'{self.prefix}.pairs')

    def get_contig_sizes(self):
        cmd = ["cphasing-rs", "chromsizes", str(self.reference), 
                "-o", str(self.contigsizes)]
        flag = run_cmd(cmd, log=f"{self.log_dir}/{self.prefix}.contigsizes.log")
        assert flag == 0, "Failed to execute command, please check log."

    def get_genome_size(self):
        from .utilities import get_genome_size

        return get_genome_size(self.reference)

    def index(self):
        """
        Create chromap index.
        """
        cmd = [self._path, '-t', str(self.threads), 
                '-k', str(self.kmer_size), '-w', str(self.window_size),
                '-i', '-r', str(self.reference), '-o', 
                str(self.index_path)]
        
        flag = run_cmd(cmd, log=f'{str(self.log_dir)}/{self.index_path}.log')
        assert flag == 0, "Failed to execute command, please check log."

        if not Path(self.index_path).exists():
            logger.error(f"Failed to create index of `{self.index_path}`.")
            sys.exit(1)

    def mapping(self):
        cmd = [self._path, '-t', str(self.threads), 
                '--preset', 'hic', 
                '-k', str(self.kmer_size), 
                '-w', str(self.window_size),
                '-q', str(self.min_quality),
                '-x', str(self.index_path), 
                '--remove-pcr-duplicates',
                '-r', str(self.reference), '-1', str(self.read1),
                '-2', str(self.read2), '-o', str(self.output_pairs)]

        flag = run_cmd(cmd, log=f'{str(self.log_dir)}/{self.prefix}.mapping.log')
        assert flag == 0, "Failed to execute command, please check log."

        if not Path(self.output_pairs).exists():
            logger.error(f"Failed to create pairs of `{self.output_pairs}`.")
            sys.exit(1)

        if is_empty(self.output_pairs):
            logger.error(f"Empty pairs of `{self.output_pairs}`.")
            sys.exit(1)    

        logger.info("Done.")

    def compress(self):
        if self.compressor == 'crabz':
            cmd = ['crabz', '-I', '-p', str(self.threads), f'{str(self.output_pairs)}']
        else:
            cmd = ['pigz', '-f', '-p', str(self.threads), f'{str(self.output_pairs)}']

        flag = run_cmd(cmd, log=f'{str(self.log_dir)}/{self.prefix}.compress.log')
        assert flag == 0, "Failed to execute command, please check log."
        logger.info("Done.")

    def pairs2pqs(self):
        from .cli import pairs2pqs
        
        args = [str(self.output_pairs) + ".gz", "-t", str(self.threads)]
        try:
            pairs2pqs.main(args=args, prog_name='pairs2pqs')
        except SystemExit as e:
            exc_info = sys.exc_info()
            exit_code = e.code
            if exit_code is None:
                exit_code = 0
            
            if exit_code != 0:
                raise e
            

    def run(self):
        self.get_contig_sizes()
        if not self.index_path.exists():
            self.index()
        else:
            logger.warning(f'The index of `{self.index_path}` was exisiting, skipped ...')
        
        self.mapping()
        self.compress()
        self.pairs2pqs()


class PoreCMapper:
    def __init__(self, reference, reads, pattern="GATC", 
                    k=15, w=10, min_quality=1, 
                    min_identity=0.8, min_length=150, 
                    max_edge=2000, realign=False,
                    additional_arguments=("-x", "map-ont"), outprefix=None,
                    threads=4, path='minimap2', log_dir='logs',
                    force=False):
        self.reference = Path(reference)
        self.index_path = Path(f'{self.reference.stem}.index')
        self.contigsizes = Path(f'{self.reference.stem}.contigsizes')
        self.reads = reads
        self.pattern = pattern
        self.additional_arguments = additional_arguments
        self.realign = realign
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self._path = path
        if which(self._path) is None:
            raise ValueError(f"{self._path}: command not found")
        
        if which('pigz') is None:
            raise ValueError(f"pigz: command not found")
        
        if which('cphasing-rs') is None:
            raise ValueError(f"cphasing-rs: command not found")


        self.prefix = Path(Path(self.reads[0]).stem).with_suffix('')
        while self.prefix.suffix in {'.fastq', 'gz', 'fq', "fasta", 'fa'}:
            self.prefix = self.prefix.with_suffix('')
       
        self.prefix = Path(str(self.prefix)) if not outprefix else outprefix


        if self.get_genome_size() > 4e9:
            self.batchsize = to_humanized(self.get_genome_size())
            self.batchsize = str(int(self.batchsize[:-1]) + 1) + self.batchsize[-1]
        else:
            self.batchsize = "4g"

        self.threads = threads
        self.min_quality = min_quality
        self.min_identity = min_identity
        self.min_length = min_length
        self.max_edge = max_edge
        self.k = k 
        self.w = w
        self.force = force
  
        self.outpaf = f'{self.prefix}.paf.gz'
        self.realign_outpaf = f'{self.prefix}.realign.paf.gz'
        if realign:
            self.outporec = f'{self.prefix}.realign.porec.gz'
            self.outpairs = f'{self.prefix}.realign.pairs.pqs'
        else:
            self.outporec = f'{self.prefix}.porec.gz'
            self.outpairs = f'{self.prefix}.pairs.pqs'
        

    def get_genome_size(self):
        from .utilities import get_genome_size

        return get_genome_size(self.reference)

    def get_contig_sizes(self):
        cmd = ["cphasing-rs", "chromsizes", str(self.reference), 
                "-o", str(self.contigsizes)]
        flag = run_cmd(cmd, log=f"{self.log_dir}/{self.prefix}.contigsizes.log")
        assert flag == 0, "Failed to execute command, please check log."

    def get_digest_bed(self):
        cmd = ["cphasing-rs", "digest", str(self.reference), "-p", str(self.pattern), 
                    "-o", f"{self.reference.stem}.slope.{self.pattern}.bed" ]
        flag = run_cmd(cmd, log=f"{self.log_dir}/{self.prefix}.digest.log")
        assert flag == 0, "Failed to execute command, please check log."

    def index(self):
        """
        Create minimap2 index
        """
        cmd = [self._path, "-t", str(self.threads), 
                "-k", str(self.k),
                "-w", str(self.w),
                "-I", self.batchsize,
                "-d", str(self.index_path),
                str(self.reference)]

        flag = run_cmd(cmd, log=f'{str(self.log_dir)}/{self.index_path}.log')
        assert flag == 0, "Failed to execute command, please check log."


    def mapping(self):
        secondary = "yes" if self.realign else "no"

        if len(self.reads) == 1:
            cmd = [self._path, 
                    '-t', str(self.threads),
                    # '-k', str(self.k),
                    # '-w', str(self.w),
                    '-c',
                    f'--secondary={secondary}',
                    '-I', self.batchsize]
            cmd.extend(list(self.additional_arguments))
            cmd.extend([str(self.reference),
                    str(self.reads[0])])
            cmd2 = ["pigz", "-c", "-p", "8"]

            logger.info('Running command:')
            logger.info('\t' + ' '.join(cmd) + ' | ' + ' '.join(cmd2)
                        + ' > ' + str(self.outpaf))
            #run_cmd(cmd)
            pipelines = []
            try:
                pipelines.append(
                    Popen(cmd, stdout=PIPE,
                    stderr=open(f'{self.log_dir}/{self.prefix}'
                    '.mapping.log', 'w'),
                    bufsize=-1)
                )

                pipelines.append(
                    Popen(cmd2, stdin=pipelines[-1].stdout,
                        stdout=open(self.outpaf, 'wb'),
                        stderr=open(f'{self.log_dir}/{self.prefix}'
                    '.mapping.log', 'w'),
                    bufsize=-1)
                )
                pipelines[-1].wait()
            
            finally:
                for p in pipelines:
                    if p.poll() is None:
                        p.terminate()
                else:
                    assert pipelines != [], \
                        "Failed to execute command, please check log."
        
        else:
            # logger.error("Developing")
            # raise ValueError("Developing")
            if self.reads[0][-3:] == ".gz":
                cat_cmd = "zcat"
            else:
                cat_cmd = "cat"
            
            cmd0 = [cat_cmd]
            cmd0.extend(self.reads)


            cmd = [self._path, 
                    '-t', str(self.threads),
                    '-k', str(self.k),
                    '-w', str(self.w),
                    '-c',
                    f'--secondary={secondary}',
                    '-I', self.batchsize,
                    ]
            cmd.extend(list(self.additional_arguments))
            cmd.append(str(self.reference))
            cmd.append("-")


            cmd2 = ["pigz", "-c", "-p", "8"]

            logger.info('Running command:')
            logger.info(f'{cat_cmd} {" ".join(self.reads)} ' + '|' + ' '.join(cmd)  + f' 2 > {self.log_dir}/{self.prefix}.mapping.log' + ' | ' + ' '.join(cmd2)
                        + ' > ' + str(self.outpaf))

            os.system(f'{cat_cmd} {" ".join(self.reads)} ' + '|' + ' '.join(cmd) + f' 2>{self.log_dir}/{self.prefix}.mapping.log' + ' | ' + ' '.join(cmd2)
                        + ' > ' + str(self.outpaf))
            
            
            # pipelines = []
            # try:
                
            #     pipelines.append(
            #         Popen(cmd0, stdout=PIPE,
            #         stderr=open(f'{self.log_dir}/{self.prefix}'
            #         '.mapping.log', 'w'),
            #         bufsize=-1, shell=True)
            #     )

            #     pipelines.append(
            #         Popen(cmd, stdin=pipelines[-1].stdout,
            #               stdout=PIPE, shell=True,
            #         stderr=open(f'{self.log_dir}/{self.prefix}'
            #         '.mapping.log', 'w'),
            #         bufsize=-1)
            #     )

            #     pipelines.append(
            #         Popen(cmd2, stdin=pipelines[-1].stdout,
            #             stdout=open(self.outpaf, 'wb'), 
            #             stderr=open(f'{self.log_dir}/{self.prefix}'
            #         '.mapping.log', 'w'),
            #         bufsize=-1)
            #     )
            #     pipelines[-1].wait()
            
            # finally:
            #     for p in pipelines:
            #         if p.poll() is None:
            #             p.terminate()
            #     else:
            #         assert pipelines != [], \
            #             "Failed to execute command, please check log."
    
    def run_realign(self):
        cmd = ["cphasing-rs", "realign", f"{self.outpaf}", "-o", f"{self.realign_outpaf}"]
        flag = run_cmd(cmd, log=f'{self.log_dir}/{self.prefix}.realign.log')
        assert flag == 0, "Failed to execute command, please check log."

    def paf2porec(self):
        paf = self.realign_outpaf if self.realign else self.outpaf
        if self.pattern:
            cmd = ["cphasing-rs", "paf2porec", f"{paf}", "-b",
                   f"{self.reference.stem}.slope.{self.pattern}.bed", "-q", 
                    f"{self.min_quality}", "-l", f"{self.min_length}", 
                    "-e", f"{self.max_edge}", "-p", f"{self.min_identity}",
                    "-o", f"{self.outporec}",
                ]
        else:
            cmd = ["cphasing-rs", "paf2porec", f"{paf}", "-q", 
                    f"{self.min_quality}", "-l", f"{self.min_length}",
                    "-e", f"{self.max_edge}", "-p", f"{self.min_identity}",
                      "-o", f"{self.outporec}"]

        flag = run_cmd(cmd, log=f'{self.log_dir}/{self.prefix}.paf2porec.log')
        assert flag == 0, "Failed to execute command, please check log."

    def porec2pairs(self):
        cmd = ["cphasing-rs", "porec2pairs", f"{self.outporec}", 
               str(self.contigsizes), "-q", f"{self.min_quality}",
                "-o", f"{self.outpairs}"]
        
        flag = run_cmd(cmd, log=f'{self.log_dir}/{self.prefix}.porec2pairs.log')
        assert flag == 0, "Failed to execute command, please check log."
    
    def run(self):
        
        if not Path(f"{self.prefix}.contigsizes").exists() or self.force:
            self.get_contig_sizes()
        else:
            logger.warning(f"The contigsizes of `{self.prefix}.contigsizes "
                           "existing, skipped ...")
        
        # if not self.index_path.exists() or self.force:
        #     self.index()
        # else:
        #     logger.warning(f'The index of `{self.index_path}` was exisiting, skipped ...')
        
        if self.pattern:
            if not op.exists(f"{self.prefix}.slope.{self.pattern}.bed") or self.force:
                self.get_digest_bed()
            else:
                logger.warning(f"The digest bed of `{self.prefix}.slope.{self.pattern}.bed "
                               "existing, skipped ...")

        if not op.exists(self.outpaf) or self.force:
            self.mapping()
            
        else:
            if is_compressed_table_empty(self.outpaf):
                logger.warning(f"Empty existing mapping result: `{self.outpaf}`, force rerun mapper.")
                self.mapping()
            else:
                logger.warning(f"The paf of `{self.outpaf} existing, skipped `reads mapping` ...")

        if self.realign:
            if not op.exists(self.realign_outpaf) or self.force:
                self.run_realign()
            else:
                logger.warning(f"The realign paf of `{self.realign_outpaf} existing, skipped `realign` ...")

        if not op.exists(self.outporec) or self.force:
            self.paf2porec()
        else:
            logger.warning(f"The porec table of {self.outporec} existing, skipped `paf2porec`...")
        
        if not op.exists(self.outpairs) or self.force:
            self.porec2pairs()
        else:
            if is_compressed_table_empty(self.outpairs):
                logger.warning(f"Empty existing pairs result: `{self.outpairs}`, force rerun porec2pairs.")
                self.porec2pairs()
            else:
                logger.warning(f"The pairs of {self.outpairs} existing, skipped `porec2pairs` ...")

        logger.info("Mapping done.")