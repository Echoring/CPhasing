#!/usr/bin/env python
# -*- coding:utf-8 -*-

"""
plot the dotplot between simulate contigs and the chromosome-level
"""

import argparse
import logging
import os
import os.path as op
import sys

import pandas as pd

import matplotlib as mpl
mpl.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns

from collections import Counter, defaultdict
from pytools import natsorted

from cphasing.core import ClusterTable
from cphasing.utilities import list_flatten

def read_chrom(contig_size, cluster_contigs):
    contig_size = dict((i.strip().split()[0], i.strip().split()[1]) 
                                for i in open(contig_size))
    contig_list = []
    contig_to_chrom = {}
    chrom_to_contig = defaultdict(list)
    for i in contig_size.keys():
        if i not in cluster_contigs:
            continue
        
        chrom = i.split(".")[0]
        if "scaffold" not in chrom.lower():
            contig_to_chrom[i] = chrom 
            chrom_to_contig[chrom].append(i)
        contig_list.append(i)
            
        

    return contig_list, chrom_to_contig

def dotplot(cluster_data, contig_list, chrom_to_contig, output):
    fig, ax = plt.subplots(figsize=(7, 7))
    K = cluster_data.values()
    
    new_K = {}
    for g in K:
        c = Counter(map(lambda x: x.split(".")[0], g))
        max_g = max(c, key=lambda x: c[x])
        new_K[max_g] = g 
    K = []
    for g in natsorted(new_K):
        k = list(filter(lambda x: "scaffold" not in x.lower(), new_K[g]))
        K.append(k)

    contig_list = list(filter(lambda x: "scaffold" not in x.lower(), contig_list))
    contig_idx_dict = dict(zip(contig_list, list(range(len(contig_list)))))
    groups = list(map(lambda x: contig_idx_dict[x], list_flatten(map(natsorted, K))))

    
    
    plt.hlines(np.cumsum(list(map(len, chrom_to_contig.values())))[:-1], 0, len(contig_list), '#bcbcbc', linewidth=1)
    plt.vlines(np.cumsum(list(map(len, K)))[:-1], 0, len(contig_list), color='#bcbcbc', linewidth=1)
    plt.scatter(contig_idx_dict.keys(), groups, color='black', s=1)
    plt.yticks(np.r_[0, np.cumsum(list(map(len, chrom_to_contig.values())))[:-1]], chrom_to_contig.keys())
    plt.xticks(np.r_[0, np.cumsum(list(map(len, K)))[:-1]], 
                list(map(lambda x: f"group{x}", range(1, len(K) + 1))),
                rotation=45)
    plt.xlim(0, len(contig_list))
    plt.ylim(0, len(contig_list))
    plt.xlabel('C-Phasing', fontsize=18)
    plt.ylabel('Real grouping', fontsize=18)

    plt.savefig(output, dpi=600, bbox_inches='tight')

def main(args):
    p = argparse.ArgumentParser(prog=__file__,
                        description=__doc__,
                        formatter_class=argparse.RawTextHelpFormatter,
                        conflict_handler='resolve')
    pReq = p.add_argument_group('Required arguments')
    pOpt = p.add_argument_group('Optional arguments')
    pReq.add_argument('cluster_table', 
            help='cluster table')
    pReq.add_argument('contig_size',
            help='contig size')
    pReq.add_argument('output',
            help='output picture')
    pOpt.add_argument('-h', '--help', action='help',
            help='show help message and exit.')
    
    args = p.parse_args(args)

    ct = ClusterTable(args.cluster_table)

    contig_list, chrom_to_contig = read_chrom(args.contig_size, set(ct.contigs))

    dotplot(ct.data, contig_list, chrom_to_contig, args.output)

if __name__ == "__main__":
    main(sys.argv[1:])
