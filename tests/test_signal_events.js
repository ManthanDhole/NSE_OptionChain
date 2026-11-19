// Regression checks against the actual dashboard event-selection function.
// Run: node tests/test_signal_events.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../ui/index.html'), 'utf8');
const code = html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(code);
const context = { Date };
vm.createContext(context);
vm.runInContext(code.slice(code.indexOf('function selectSignalEvent'), code.indexOf('function volumeCell')), context);
const shift = { direction: 'SHIFTED FROM TOP TO BOTTOM', from_strikes: [25200], to_strikes: [25100], at: '2026-11-13T09:31:15+05:30' };
const transition = { from_status: 'WTB', to_status: 'WTT', from_strikes: [25200], to_strikes: [25100], at: shift.at };
function choose(s, t, state) { return context.selectSignalEvent(s, t, state); }
assert.equal(choose(shift, transition, 'WTT').event, shift, 'Simultaneous WTB->WTT must show direction');
const upward = { ...shift, direction: 'SHIFTED FROM BOTTOM TO TOP', from_strikes: [25000], to_strikes: [25100] };
assert.equal(choose(upward, { ...transition, from_status: 'WTT', to_status: 'WTB' }, 'WTB').event, upward);
assert.equal(choose(shift, { ...transition, at: '2026-11-13T09:30:00+05:30' }, 'WTT').event, shift, 'Old status cannot mask new shift');
assert.equal(choose(shift, { ...transition, at: '2026-11-13T09:32:00+05:30' }, 'WTT').isTransition, true);
for (const [from, to] of [['WTT', 'STRONG'], ['WTB', 'STRONG'], ['STRONG', 'WTT'], ['STRONG', 'WTB']]) {
  assert.equal(choose(shift, { ...transition, from_status: from, to_status: to }, to).isTransition, true);
}
assert.equal(choose(shift, transition, 'STRONG').event, shift, 'Ignore status event that does not match current status');
assert.equal(choose(null, transition, 'WTT').event, transition);
assert.equal(choose(null, null, 'STRONG').event, null);
// Verify the final label uses the chosen event's direction, strikes and time.
function makeElement() {
  return { children: [], value: '', classList: { toggle() {} },
    set textContent(value) { this.value=value; this.children=[]; },
    get textContent() { return this.value+this.children.map(c=>c.textContent).join(''); },
    replaceChildren() { this.value=''; this.children=[]; },
    append(...children) { this.children.push(...children); }
  };
}
const elements = {};
for (const prefix of ['call', 'put']) for (const name of ['Signal', 'SignalDetail', 'SignalBox', 'VolumeShift']) {
  elements[prefix + name] = makeElement();
}
for(const id of ['marketSentiment','marketSentimentRule','marketSentimentCell'])elements[id]=makeElement();
Object.assign(context, {
  $: id => elements[id], fmt: String,
  document: { createElement: makeElement, createTextNode: text => ({textContent:text}) },
  current: { volume_shifts: { CE: shift, PE: upward }, strength_transitions: { CE: transition, PE: { ...transition, from_status: 'WTT', to_status: 'WTB' } } },
  volumeSignal: (_, side) => ({ message: side === 'CE' ? 'WEAK TOWARDS TOP' : 'WEAK TOWARDS BOTTOM', detail: 'test', active: true }),
});
context.updateVolumeSignals([]);
assert.equal(elements.callSignal.textContent,'CALL · WEAK TOWARDS TOP');
assert.equal(elements.putSignal.textContent,'PUT · WEAK TOWARDS BOTTOM');
assert.match(elements.callVolumeShift.textContent, /SHIFTED FROM TOP TO BOTTOM · 25200 TO 25100 · .*09:31:15/);
assert.match(elements.putVolumeShift.textContent, /SHIFTED FROM BOTTOM TO TOP · 25000 TO 25100/);
for (const prefix of ['call', 'put']) {
  const children=elements[prefix+'VolumeShift'].children;
  assert.equal(children.length,2);
  assert.equal(children[0].className,'event-badge');
  assert(!children[0].textContent.includes('25100'));
  assert(children[1].textContent.includes('25100'));
  assert.equal(children[1].className,undefined);
}
console.log('Signal event and status-only badge regression checks passed.');
