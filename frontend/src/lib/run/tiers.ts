// The hybrid's tiers (backend agent/second_opinion.py, decision 97). The second opinion's
// notes that agree or disagree with an opening-hours finding are folded into that finding's
// card, so the traveller sees one card per problem with how sure we are; its own finds are
// "worth checking". Without a second opinion in the run, nothing gets a tier.
import type { Violation } from "@/lib/api/types";

export type Tier = "confirmed" | "disputed" | "unconfirmed";

export interface TierInfo {
  tier: Tier;
  note: Violation | null; // the second opinion's note, with its source
}

export interface Tiers {
  byViolation: Map<string, TierInfo>;
  folded: Set<string>; // notes shown inside another card, not on their own
}

const CHECKED = new Set(["opening_hours", "closures"]);
const AGREEMENT: Record<string, Tier> = { confirms: "confirmed", disputes: "disputed" };

export function opinionTiers(violations: Violation[]): Tiers {
  const notes = new Map<string, Violation>(); // stop id -> agreeing or disagreeing note
  for (const v of violations) {
    if (v.check_name === "second_opinion" && AGREEMENT[String(v.evidence.agreement)]) {
      for (const id of v.stop_ids) notes.set(id, v);
    }
  }
  const tiers: Tiers = { byViolation: new Map(), folded: new Set() };
  for (const v of violations) {
    if (v.check_name === "second_opinion" && v.evidence.agreement === "only_second_opinion") {
      tiers.byViolation.set(v.id, { tier: "unconfirmed", note: null });
      continue;
    }
    if (!CHECKED.has(v.check_name) || v.severity === "info") continue;
    const note = v.stop_ids.map((id) => notes.get(id)).find((n) => n !== undefined);
    if (!note) continue;
    tiers.byViolation.set(v.id, { tier: AGREEMENT[String(note.evidence.agreement)], note });
    tiers.folded.add(note.id);
  }
  return tiers;
}
