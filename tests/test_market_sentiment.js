// Run: node tests/test_market_sentiment.js
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../ui/index.html'),'utf8');
const code=html.match(/<script>([\s\S]*?)<\/script>/)[1];new vm.Script(code);
const elements={marketSentiment:{},marketSentimentRule:{},marketSentimentCell:{}};
const ctx={$:id=>elements[id],number:v=>v==null?null:Number(v),fmt:String};vm.createContext(ctx);
vm.runInContext(code.slice(code.indexOf('function marketSentiment'),code.indexOf('function updateVolumeSignals')),ctx);
const WTT='WEAK TOWARDS TOP',WTB='WEAK TOWARDS BOTTOM',STRONG='STRONG';
for(const [call,put,text,rule] of [
 [WTT,WTT,'Nifty is in Bull Run',1],
 [WTB,WTB,'Nifty is in Blood Bath',2],
 [WTT,STRONG,'Nifty is in Slightly Bullish',3],
 [STRONG,WTT,'Nifty is in Slightly Bullish',3],
 [STRONG,WTB,'Nifty is in Slightly Bearish',4],
 [WTB,STRONG,'Nifty is in Slightly Bearish',4],
 [STRONG,STRONG,'Nifty is Neutral',5],
 [WTT,WTB,'Nifty is Both Side Risky',6],
 [WTB,WTT,'Nifty is Both Side Risky',6],
 ['NO WEAK SIGNAL',STRONG,'NO MATCHING RULE',null],
 ['MIXED STRIKE DIRECTION',WTT,'NO MATCHING RULE',null],
 ['NOT ENOUGH VOLUME DATA',WTT,'WAITING FOR DATA',null],
 [undefined,undefined,'WAITING FOR DATA',null],
]){
 const s=ctx.marketSentiment(call,put);assert.equal(s.text,text);assert.equal(s.rule,rule);
 ctx.updateMarketSentiment({CE:{message:call},PE:{message:put}});assert.equal(elements.marketSentiment.textContent,text);
}
// Test actual volume classification flows into sentiment, including strict 75%.
vm.runInContext(code.slice(code.indexOf('function volumeRanks'),code.indexOf('function selectSignalEvent')),ctx);
const rows=[{strikePrice:25000,CE_totalTradedVolume:100,PE_totalTradedVolume:100},{strikePrice:25100,CE_totalTradedVolume:80,PE_totalTradedVolume:80}];
assert.equal(ctx.marketSentiment(ctx.volumeSignal(rows,'CE').message,ctx.volumeSignal(rows,'PE').message).rule,1);
rows[1].PE_totalTradedVolume=74;
assert.equal(ctx.marketSentiment(ctx.volumeSignal(rows,'CE').message,ctx.volumeSignal(rows,'PE').message).rule,3);
rows[1].PE_totalTradedVolume=75;
assert.equal(ctx.marketSentiment(ctx.volumeSignal(rows,'CE').message,ctx.volumeSignal(rows,'PE').message).rule,null);
console.log('All sentiment rule combinations, displayed labels, and volume integration checks passed.');
