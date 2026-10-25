// Doris's shared AP desk. Surfaces mirrors every Decision and every line of a callback here; every open
// dashboard subscribes and updates the instant a row lands, so several clerks (and the judges' screen) watch
// the same queue and the same call live. Only Doris writes: the identity that published the module.
import { schema, table, t, SenderError } from 'spacetimedb/server';

const spacetimedb = schema({
  config: table(
    { name: 'config' },
    { id: t.u32().primaryKey(), writer: t.identity() }
  ),
  decision: table(
    { name: 'decision', public: true },
    {
      decisionId: t.string().primaryKey(),
      invoiceId: t.string(),
      decision: t.string(),
      supersedes: t.string(),
      riskScore: t.f64(),
      headline: t.string(),
      vendorClaimed: t.string(),
      amount: t.string(),
      createdAt: t.string(),
      seq: t.u64(),
    }
  ),
  callEvent: table(
    { name: 'call_event', public: true },
    {
      id: t.u64().primaryKey().autoInc(),
      decisionId: t.string().index('btree'),
      who: t.string(),
      text: t.string(),
      at: t.f64(),
    }
  ),
  viewer: table(
    { name: 'viewer', public: true },
    { identity: t.identity().primaryKey(), since: t.timestamp() }
  ),
});
export default spacetimedb;

const cap = (s: string, n: number) => (s.length > n ? s.slice(0, n) : s);

function requireWriter(ctx: any) {
  const c = ctx.db.config.id.find(0);
  if (!c || !c.writer.equals(ctx.sender)) throw new SenderError('only Doris writes to the desk');
}

export const init = spacetimedb.init(ctx => {
  ctx.db.config.insert({ id: 0, writer: ctx.sender });
});

export const onConnect = spacetimedb.clientConnected(ctx => {
  if (!ctx.db.viewer.identity.find(ctx.sender)) ctx.db.viewer.insert({ identity: ctx.sender, since: ctx.timestamp });
});

export const onDisconnect = spacetimedb.clientDisconnected(ctx => {
  ctx.db.viewer.identity.delete(ctx.sender);
});

export const upsertDecision = spacetimedb.reducer(
  {
    decisionId: t.string(), invoiceId: t.string(), decision: t.string(), supersedes: t.string(), riskScore: t.f64(),
    headline: t.string(), vendorClaimed: t.string(), amount: t.string(), createdAt: t.string(), seq: t.u64(),
  },
  (ctx, row) => {
    requireWriter(ctx);
    const clean = {
      ...row,
      decisionId: cap(row.decisionId, 64), invoiceId: cap(row.invoiceId, 64), decision: cap(row.decision, 16),
      supersedes: cap(row.supersedes, 64), headline: cap(row.headline, 300), vendorClaimed: cap(row.vendorClaimed, 120),
      amount: cap(row.amount, 40), createdAt: cap(row.createdAt, 40),
    };
    if (ctx.db.decision.decisionId.find(clean.decisionId)) ctx.db.decision.decisionId.update(clean);
    else ctx.db.decision.insert(clean);
  }
);

export const addCallEvent = spacetimedb.reducer(
  { decisionId: t.string(), who: t.string(), text: t.string(), at: t.f64() },
  (ctx, e) => {
    requireWriter(ctx);
    ctx.db.callEvent.insert({ id: 0n, decisionId: cap(e.decisionId, 64), who: cap(e.who, 16), text: cap(e.text, 400), at: e.at });
  }
);
