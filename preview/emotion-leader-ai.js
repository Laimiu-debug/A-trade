/* Stock buttons are shortcuts into the shared top-right AI conversation. */
(() => {
  if(typeof window==='undefined'||typeof AT==='undefined'||!AT.isDesktop)return;
  function button(stock) {
    return `<button type="button" class="analysis-tools-button emotion-ai-button" data-emotion-leader-ai="${AT.escape(stock.symbol)}" aria-label="${AT.escape('分析此股 '+(stock.name||stock.symbol))}" ${AT.emotionCycle?.getLeaderScope()?.busy?'disabled':''}>分析此股</button>`;
  }
  document.addEventListener('click',event=>{
    if(AT.state.page!=='T10')return;
    const element=event.target.closest('[data-emotion-leader-ai]');if(!element||element.disabled)return;
    event.preventDefault();event.stopPropagation();
    const scope=AT.emotionCycle?.getLeaderScope(),data=scope?.data;
    const theme=data?.themes?.find(row=>row.id===scope.themeId);
    const stock=[...(theme?.leaders||data?.leaders||[]),...(data?.yesterdayLeaders||[])].find(row=>row.symbol===element.dataset.emotionLeaderAi);
    if(stock&&!scope.busy)AT.pageAI?.analyzeStock(stock);
  });
  AT.emotionLeaderAI={button};
})();
