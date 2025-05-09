#!/usr/bin/env python
# -*- coding:utf-8 -*-

"""
calculate the cis/trans ratio according from real order cool
"""

import argparse
import logging
import os
import os.path as op
import sys

import cooler 
import matplotlib as mpl
mpl.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
import numpy as np 

from collections import defaultdict
from itertools import combinations
from math import sqrt

def wi_test(data1, data2):
    """
    Wilcoxon rank-sum tests
    return: pvalue
    """
    from scipy import stats
    wi = stats.ranksums(data1, data2)
    return wi.pvalue

def as_si(x, ndp):
    """
    Scientific count format function, use x replace e.
    """
    s = '{x:0.{ndp:d}e}'.format(x=x, ndp=ndp)
    m, e = s.split('e')
    return r'{m:s}\times 10^{{{e:d}}}'.format(m=m, e=int(e))

def main(args):
    p = argparse.ArgumentParser(prog=__file__,
                        description=__doc__,
                        formatter_class=argparse.RawTextHelpFormatter,
                        conflict_handler='resolve')
    pReq = p.add_argument_group('Required arguments')
    pOpt = p.add_argument_group('Optional arguments')
    pOpt.add_argument('-c', '--cool', 
            nargs="+",
            help='Path to cool file',
            required=True)
    pOpt.add_argument("-hl", "--homo_list",
                    
            help="homolog chroms rellationship",
            required=True)
    pOpt.add_argument('--wi_test', action="store_true", default=False,
                      help="plot the wi-test, only support two cool file")
    pOpt.add_argument('-h', '--help', action='help',
        help='show help message and exit.')
    
    args = p.parse_args(args)

    results = []
    for cool_file in args.cool:
        cool = cooler.Cooler(cool_file)
        matrix = cool.matrix(balance=False, sparse=True)

        homo_chroms = [i.strip().split() for i in open(args.homo_list) if i.strip()]

        haps = [i[-1] for i in homo_chroms[0]]

        total_cis = 0 
        total_trans = 0
        res = []
        for homo in homo_chroms:
            homo.sort()
            for pair in combinations(homo, 2):
                try:
                    cis1 = matrix.fetch(pair[0]).sum()
                    cis2 = matrix.fetch(pair[1]).sum()
                    trans = matrix.fetch(*pair).sum()
                except:
                    continue 

                data = trans / sqrt(cis1 * cis2)

                res.append((pair, data))

        results.append(res)
    
    db = defaultdict(list)
    for res in results:
        for pair, value in res:
            hap_pair = tuple(map(lambda x: x[-1], pair))
            db[hap_pair].append(value)
        
    res_df = pd.DataFrame(db).T.mean(axis=1)
    res_df.to_csv("h-trans.pairwise.tsv", header=None, index=True, sep='\t')
    res_matrix = res_df.unstack(level=0)
    res_matrix.to_csv("h-trans.pairwise.matrix", header=True, index=True, sep='\t')
    plt.rcParams['font.family'] = 'Arial'
    sns.heatmap(res_matrix, cmap='coolwarm', vmax=1.0, vmin=0.0, center=0.5)
    plt.savefig("h-trans.heatmap.png")
    # plt.rcParams['font.family'] = 'Arial'
    # fig, ax = plt.subplots(figsize=(4, 5))
    # boxprops_r = dict(facecolor='#a83836',color='black', linewidth=1.5)
    # boxprops_b = dict(facecolor='#265e8a',color='black', linewidth=1.5)
    # boxprops = dict(color='black', linewidth=1.5)
    # medianprops=dict(color='black', linewidth=2.5)
    # whiskerprops = dict(linestyle='--')

    # if args.wi_test:
    #     pvalue = wi_test(results[0], results[1])
    
    # # a = ax.boxplot(results[0], showfliers=False, patch_artist=True, notch=True, widths=0.4,
    # #            boxprops=boxprops_r, medianprops=medianprops, whiskerprops=whiskerprops)

    # # a_upper_extreme = [ item.get_ydata() for item in a['whiskers']][1][1]
    # # a_bottom_extreme = [ item.get_ydata() for item in a['whiskers']][0][1]

    # # b = ax.boxplot([[], results[1]], showfliers=False, patch_artist=True, notch=True, widths=0.4,
    # #            boxprops=boxprops_b, medianprops=medianprops, whiskerprops=whiskerprops)
    # # b_upper_extreme = [ item.get_ydata() for item in b['whiskers']][3][1]
    # # b_bottom_extreme = [ item.get_ydata() for item in b['whiskers']][2][1]

    # # # for patch, color in zip(bplot['boxes'], ['#a83836',  '#8dc0ed', '#df8384', ]):
    # # #     patch.set_facecolor(color)
    # # max_upper = max(a_upper_extreme, b_upper_extreme)
    # # min_upper = min(a_upper_extreme, b_upper_extreme)
    # # min_bottom = min(a_bottom_extreme, b_bottom_extreme)
    # # h = max_upper/5

    # # if max_upper == a_upper_extreme:
    # #     y1 = max_upper + max_upper/10
    # #     y2 = min_upper + max_upper/10
    # # else:
    # #     y1 = min_upper + max_upper/10
    # #     y2 = max_upper + max_upper/10

    # # ax.plot([1,1,2,2], [y1, max_upper + h, max_upper + h, y2], linewidth=1.0, color='black')
    # # ax.text((1 + 2)*.5, max_upper + max_upper/4.5, r'$P = {0:s}$'.format(as_si(pvalue, 2)), ha='center', va='bottom' )
    # colors = ['#df8384', '#8dc0ed']
    # # colors = ['#a83836', '#a83836', '#a83836', '#a83836',  
    # #             '#df8384', '#df8384', '#df8384', '#df8384', 
    # #             "#253761", '#8dc0ed',]
    # # colors = ['#a83836',  '#df8384', "#253761", '#8dc0ed',]
    # # colors = ['#df8384']
    # ax = sns.violinplot(data=results, ax=ax, palette=colors, alpha=1)
    # ax.set_xticks([0, 1])
    # # ax.set_xticklabels(['MAPQ>=1', "MAPQ>=2"])
    # ax.set_xticklabels(['Raw', 'Removed \n$\mathit{trans}$'])
    # max_y = max(max(results)) * 1.5
    # plt.ylim(-0.001, max_y)
    # # ax.set_xticklabels(["Before\n realign", "After\n realign", ], fontsize=20)
    
    # # ax.set_xticks([0, 1, 2])
    # # ax.set_xticklabels(["Before\n realign", "After\n realign", "After\n realign2"], fontsize=20)

    # # ax.set_xticks([0, 1, 2, 3, 4])
    # # ax.set_xticklabels([2, 4, 6, 8, 12], fontsize=20)

    # # ax.set_xticks([0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
    # # ax.set_xticklabels(["k15 w5", "k15_w10", "k17_w7", "k27_w14", "k15 w5", "k15_w10", "k17_w7", "k27_w14", "Hi-C $\it{Dpn}$II", "Hi-C Arima"], 
    #                 #    fontsize=8, rotation=45, ha="right")
    # # ax.set_xticks([0, 1, 2, 3])
    # # ax.set_xticklabels(["Pore-C\n$\it{Hin}$dIII", "Pore-C\n$\it{Dpn}$II", "Hi-C\n$\it{Dpn}$II", "Hi-C\nArima"], fontsize=20)

    # # ax.set_xticks([0, 1])
    # # ax.set_xticklabels(["Pore-C", "Hi-C", ], fontsize=20)
    # # max_y = max(max(results)) * 1.3
    # # plt.ylim(-0.1, max_y)
    # if args.wi_test:
    #     plt.text(0.5, max_y * 0.95,f'Wilcox test p-value < {pvalue:.1e}', ha='center', fontsize=12)
    

    # # ax.set_xticks([0, 1, 2, 3])
    # # ax.set_xticklabels(["Pore-C\n$\it{Hin}$dIII", "Pore-C\n$\it{Dpn}$II", "Hi-C\n$\it{Dpn}$II", "Hi-C\nArima"], fontsize=20)
    # plt.xticks(fontsize=18)
    # plt.yticks(fontsize=16)
    # ax.set_ylabel("Normalized contacts", fontsize=22)
    # plt.savefig('boxplot.png', dpi=600, bbox_inches='tight')
    # plt.savefig('boxplot.pdf', dpi=600, bbox_inches='tight')

if __name__ == "__main__":
    main(sys.argv[1:])
