// Run: node tests/test_price_highlights.js
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../ui/index.html'),'utf8'),code=html.match(/<script>([\s\S]*?)<\/script>/)[1];new vm.Script(code);
const ctx={Intl,Date,Set,number:v=>v==null||v==='N/A'?null:Number(v),fmt:String,cell:(text,className)=>({text,className})};vm.createContext(ctx);
vm.runInContext(code.slice(code.indexOf('function priceHighlightContext'),code.indexOf('function render(snapshot)')),ctx);
const rows=[25350,25400,25450,25500,25550,25650].map(strikePrice=>({strikePrice}));
for(const [time,low,high] of [['2026-11-18T21:00:00Z',93,105],['2026-11-16T21:00:00Z',23,34]]){
 const c=ctx.priceHighlightContext(rows,25510,new Date(time));assert.deepEqual([...c.strikes],[25450,25550]);
 for(const side of ['call','put']){
  for(const [value,match] of [[low,false],[low+.01,true],[high,true],[high+.01,false],[null,false],['N/A',false]]){
   assert.equal(ctx.priceCell(value,side,25450,c).className.includes('price-window-match'),match);
  }
  for(const strike of [25350,25400,25500,25650])assert(!ctx.priceCell(high,side,strike,c).className.includes('price-window-match'));
 }
}
const off=ctx.priceHighlightContext(rows,25510,new Date('2026-11-18T10:00:00+05:30'));assert.equal(off.band,null);
assert.deepEqual([...ctx.priceHighlightContext(rows,25450,new Date('2026-11-19T10:00:00+05:30')).strikes],[25450]);
assert.equal(ctx.priceHighlightContext([],25500,new Date('2026-11-19T10:00:00+05:30')).strikes.size,0);
assert(html.includes('.highlights-off td.call-cell.price-window-match'));
assert(html.includes('.highlights-off td.put-cell.price-window-match'));
console.log('Near-spot price checks passed: IST weekdays, strict/inclusive bounds, both sides, nearest 50 strikes, empty data, toggle overrides.');
