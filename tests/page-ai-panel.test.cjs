const {test}=require('node:test');
const assert=require('node:assert/strict');
const {markdown,suggestions,historyFor}=require('../preview/page-ai-panel.js');

test('model prose is escaped even inside Markdown, code fences and unfinished fences',()=>{
  const input='# 解读\n**<img src=x onerror="alert(1)">**\n- <script>run()</script>\n[打开](javascript:evil())\n```html\n</pre><iframe src="https://example.invalid"></iframe>\n```\n`<svg onload=run()>`\n```\n<b>末尾';
  const html=markdown(input);
  assert.doesNotMatch(html,/<(?:img|script|iframe|svg|a\b|b\b)/);
  assert.match(html,/&lt;img/);
  assert.match(html,/<h4>解读<\/h4>/);
  assert.match(html,/<strong>&lt;img/);
  assert.match(html,/&lt;\/pre&gt;&lt;iframe/);
  assert.equal((html.match(/<pre>/g)||[]).length,(html.match(/<\/pre>/g)||[]).length);
});

test('followup history stays within service limits and retains complete recent pairs',()=>{
  const source=Array.from({length:12},(_,i)=>[{role:'user',content:`问题${i}`+'问'.repeat(3990)},{role:'assistant',content:`回答${i}`+'答'.repeat(15000),source:'2026-10-09'}]).flat();
  const history=historyFor(source);
  assert.ok(history.length<=16&&history.length%2===0);
  assert.ok(history.every(row=>row.content.length<=4000));
  assert.ok(history.reduce((sum,row)=>sum+row.content.length,0)<=30000);
  assert.equal(history[0].role,'user');
  assert.equal(history.at(-1).role,'assistant');
  assert.match(history.at(-1).content,/回答11/);
  assert.match(history.at(-1).content,/2026-10-09/);
  assert.equal(source.length,24,'source conversations are not truncated in place');
});

test('leadership questions require cross-day evidence, counterexamples and clear sampling',()=>{
  const questions=suggestions({id:'T08'}).map(item=>item[1]).join('\n');
  assert.match(questions,/晋级与断板/);
  assert.match(questions,/反例、样本数/);
  assert.match(questions,/最近 5 个与前 5 个/);
  assert.match(questions,/样本不足/);
  assert.notDeepEqual(suggestions({id:'T08'}),suggestions({id:'T04'}));
});
