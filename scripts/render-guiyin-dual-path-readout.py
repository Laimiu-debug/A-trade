"""Render already audited local research results; no data fetching or strategy run."""
import gzip
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'artifacts/strategy-search/guiyin-dual-path-system-v1/plot-runtime'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
import matplotlib.dates as mdates
from datetime import date

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/strategy-search/guiyin-dual-path-system-v1'/('attempt-'+(sys.argv[1] if len(sys.argv)>1 else '001'))


def read(path):
    with (gzip.open(path,'rt',encoding='utf-8') if str(path).endswith('.gz') else path.open('r',encoding='utf-8')) as f:return json.load(f)


def main():
    proof=read(OUT/'independent-account-validation.json')
    assert proof['passed']
    readout=read(OUT/'readout.json')
    font=Path('C:/Windows/Fonts/msyh.ttc')
    if font.exists():font_manager.fontManager.addfont(str(font));plt.rcParams['font.family']='Microsoft YaHei'
    plt.rcParams.update({'axes.spines.top':False,'axes.spines.right':False,'axes.unicode_minus':False,'font.size':10})
    fig,(ax,hist)=plt.subplots(1,2,figsize=(13,5.5),gridspec_kw={'width_ratios':[1.35,1]})
    for policy,label,color in [('complete','完整体系','#146a86'),('Aonly','仅起势路径','#cb7b2e'),('Bonly','仅核心回撤路径','#6d609e')]:
        r=read(OUT/'primary'/policy/'result.json.gz');initial=r['config']['initialCapital']
        ds=[date.fromisoformat(v['date']) for v in r['curve']]
        ax.plot(ds,[(v['equity']/initial-1)*100 for v in r['curve']],label=label,color=color,lw=1.8)
        if policy=='complete':
            ax.plot(ds,[(v['benchmarkEquity']/initial-1)*100 for v in r['curve']],label='沪深300',color='#7b8187',ls='--',lw=1.5)
            cycles=r['closedTrades']
    ax.axhline(0,color='#adb5bb',lw=.7)
    ax.set_title('持续资金账户：扣费后累计收益',loc='left',fontweight='bold')
    ax.set_ylabel('累计收益（%）');ax.xaxis.set_major_locator(mdates.MonthLocator(interval=6));ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax.grid(axis='y',alpha=.18);ax.legend(frameon=False,fontsize=9)
    values=[t['returnPct'] for t in cycles]
    if values:
        edges=np.arange(np.floor(min(values)),np.ceil(max(values))+1.01,1.)
        hist.hist([v for v in values if v<=0],bins=edges,color='#ba5c51',alpha=.85,label='亏损与持平')
        hist.hist([v for v in values if v>0],bins=edges,color='#338e79',alpha=.85,label='盈利')
    hist.axvline(0,color='#333',lw=.8);hist.set_xlabel('完整交易周期净收益（%）');hist.set_ylabel('交易周期数');hist.grid(axis='y',alpha=.18)
    hist.set_title(f'完整体系全部已平仓交易：{len(values)} 笔',loc='left',fontweight='bold');hist.legend(frameon=False,fontsize=9)
    mainrow=next(r for r in readout['rows'] if r['period']=='primary' and r['policy']=='complete')
    ratio=lambda v:'缺少分母' if v is None else f'{v:.2f}'
    fig.suptitle('归因启发的两类入场体系 · 固定规则验证',x=.06,ha='left',fontweight='bold',fontsize=16)
    fig.text(.06,.88,f"完整体系  盈亏比 {ratio(mainrow['payoffRatio'])}  |  PF {ratio(mainrow['profitFactor'])}  |  最大回撤 {mainrow['maxDrawdownPct']:.2f}%",fontsize=11)
    fig.text(.06,.035,'2024-09-24—2026-10-15；行业轮动日线版本；历史数据已多次暴露，未证明未来实盘收益。',fontsize=9,color='#515b65')
    fig.subplots_adjust(left=.07,right=.98,bottom=.18,top=.78,wspace=.28)
    fig.savefig(OUT/'readout.png',dpi=170,facecolor='white');plt.close(fig)


if __name__=='__main__':main()
