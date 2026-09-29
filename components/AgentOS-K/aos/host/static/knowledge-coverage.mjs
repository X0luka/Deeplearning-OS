// Counts describe review coverage, not file ingestion or knowledge mastery.
export function reviewCoverage(graph) {
  const records = graph.records || {};
  const changed = new Set(graph.changed || []);
  const needsReview = new Set(graph.pending || []);
  let reviewed=0, classified=0, unclassified=0;
  const primaryCounts={};
  for (const [key, record] of Object.entries(records)) {
    const stale=changed.has(key)||(record.evidence||[]).some(e=>changed.has(e.key)||!records[e.key]);
    const valid=record.reviewed_at&&['classified','needs_context'].includes(record.review_state);
    if (stale||!valid) {needsReview.add(key);continue;}
    reviewed++;
    if (record.primary&&record.topics?.includes(record.primary)) {
      classified++;primaryCounts[record.primary]=(primaryCounts[record.primary]||0)+1;
    } else unclassified++;
  }
  return {reviewed,classified,unclassified,pending:Math.max(0,graph.total-reviewed),
    needsReviewKeys:[...needsReview],primaryCounts};
}
