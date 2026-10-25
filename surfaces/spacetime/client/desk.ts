// The browser side of Doris's shared desk. It subscribes to the desk on SpacetimeDB and, the moment a row lands
// that matters to this page (a follow-up to the decision on screen, a new line of its call, or any new decision
// on the live queue), tells the page to refresh at once. The page then asks the dashboard for the decision
// itself: the detector stays the source of truth, and nothing from the desk is ever written into the page as HTML.
import { DbConnection } from './module_bindings';

type Cfg = { uri: string; db: string };

function cfg(): Cfg | null {
  const m = document.querySelector('meta[name="doris-desk"]');
  try { return m ? (JSON.parse(m.getAttribute('content') || '') as Cfg) : null; } catch { return null; }
}

function status(text: string): void {
  const el = document.getElementById('desk-status');
  if (el) el.textContent = text;
}

function changed(): void {
  window.dispatchEvent(new Event('doris:changed'));
}

const c = cfg();
if (c) {
  const body = document.body;
  const chain = new Set((body.getAttribute('data-chain') || '').split(',').filter(Boolean));
  const liveQueue = body.getAttribute('data-poll') === '/latest/state';
  let applied = false;
  let token: string | undefined;
  try { token = localStorage.getItem('doris-desk-token') || undefined; } catch { token = undefined; }

  const watching = (conn: DbConnection) => {
    const n = Array.from(conn.db.viewer.iter()).length;
    status(`Live desk on SpacetimeDB · ${n} watching`);
  };

  DbConnection.builder()
    .withUri(c.uri)
    .withDatabaseName(c.db)
    .withToken(token)
    .onConnect((conn, _identity, newToken) => {
      try { localStorage.setItem('doris-desk-token', newToken); } catch { /* private window: a new identity each time */ }
      (window as any).__dorisDeskLive = true;
      conn.db.decision.onInsert((_ctx, row) => {
        if (!applied) return;
        if (liveQueue || (row.supersedes && chain.has(row.supersedes))) changed();
      });
      conn.db.callEvent.onInsert((_ctx, row) => {
        if (applied && chain.has(row.decisionId)) changed();
      });
      conn.db.viewer.onInsert(() => watching(conn));
      conn.db.viewer.onDelete(() => watching(conn));
      conn.subscriptionBuilder()
        .onApplied(() => { applied = true; watching(conn); })
        .subscribe(['SELECT * FROM decision', 'SELECT * FROM call_event', 'SELECT * FROM viewer']);
    })
    .onConnectError(() => { (window as any).__dorisDeskLive = false; status('Live desk offline: refreshing on a timer'); })
    .onDisconnect(() => { (window as any).__dorisDeskLive = false; status('Live desk offline: refreshing on a timer'); })
    .build();
}
