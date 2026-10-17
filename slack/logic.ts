export type Choice = "accept" | "refuse" | "abstain";
export type Ballot = {
  id: string;
  poll: string;
  revision: string;
  actor: string;
  stamp: string;
  action: string;
};
export type Project = {
  id: string;
  repo: string;
  pr: number;
  issue: number;
  revision: string;
  head: string;
  channel: string;
  kind: "EP" | "CLAIM";
  members: number;
  quorum: number;
  status: "waiting" | "processing" | "accepted" | "refused";
  title: string;
  groups: string;
  leads: string;
  contributors: string;
  description: string;
  cutoff?: string;
  note?: string;
  delivery?: string;
  content_hash?: string;
  test_buttons?: boolean;
};
export function tally(project: Project, events: Ballot[]) {
  const relevant = events.filter((e) =>
    e.poll === project.id && e.revision === project.revision &&
    (!project.cutoff || Number(e.stamp) <= Number(project.cutoff))
  )
    .sort((a, b) =>
      Number(a.stamp) - Number(b.stamp) || a.id.localeCompare(b.id)
    );
  const votes = new Map<string, Choice>();
  let simulation: Choice | undefined;
  let force: Choice | undefined;
  for (const event of relevant) {
    const [kind, choice] = event.action.split(":");
    if (!["accept", "refuse", "abstain"].includes(choice)) continue;
    if (kind === "force" && choice !== "abstain") force = choice as Choice;
    if (kind === "test" && project.test_buttons) simulation = choice as Choice;
    if (kind === "vote") {
      simulation = undefined;
      votes.set(event.actor, choice as Choice);
    }
  }
  const counts = { accept: 0, refuse: 0, abstain: 0 };
  if (simulation) counts[simulation] = project.quorum;
  else for (const choice of votes.values()) counts[choice]++;
  const participation = counts.accept + counts.refuse + counts.abstain;
  const decision = force ??
    (participation < project.quorum || counts.accept === counts.refuse
      ? "waiting"
      : counts.accept > counts.refuse
      ? "accept"
      : "refuse");
  return { ...counts, participation, simulation, force, decision };
}
export function escape(text: string) {
  return text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(
    ">",
    "&gt;",
  );
}
export function chunks(text: string, size = 2800): string[] {
  const chars = Array.from(text);
  const result: string[] = [];
  for (let i = 0; i < chars.length; i += size) {
    result.push(chars.slice(i, i + size).join(""));
  }
  return result.length ? result : ["None"];
}
