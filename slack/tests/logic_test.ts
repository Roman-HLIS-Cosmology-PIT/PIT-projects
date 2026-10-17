import { type Ballot, chunks, escape, type Project, tally } from "../logic.ts";
import cases from "./cases.json" with { type: "json" };

function equal(actual: unknown, expected: unknown) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(
      `Expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`,
    );
  }
}
const project = {
  id: "pr-99",
  revision: "r1",
  members: 5,
  quorum: 3,
} as Project;
const event = (actor: string, action: string, stamp: number): Ballot => ({
  id: String(stamp),
  poll: project.id,
  revision: project.revision,
  actor,
  action,
  stamp: String(stamp),
});
for (const scenario of cases) {
  Deno.test(scenario.name, () => {
    const p = {
      ...project,
      members: scenario.members,
      quorum: Math.floor(scenario.members / 2) + 1,
      test_buttons: scenario.test_buttons ?? true,
    };
    const events = scenario.events.map(([actor, action], i) =>
      event(actor, action, i + 1)
    );
    equal(tally(p, events).decision, scenario.expect);
  });
}
Deno.test("older revisions and post-cutoff events do not count", () => {
  const events = [event("a", "vote:accept", 1), event("b", "vote:accept", 2), {
    ...event("c", "vote:accept", 3),
    revision: "r0",
  }, event("d", "vote:accept", 5)];
  equal(tally({ ...project, cutoff: "4" }, events).participation, 2);
});
Deno.test("out-of-order deliveries preserve latest ballot", () => {
  const events = [event("a", "vote:accept", 3), event("a", "vote:refuse", 1)];
  equal(tally(project, events).accept, 1);
});
Deno.test("submitted text cannot inject Slack mentions", () => {
  equal(escape("<!channel> & <@U123>"), "&lt;!channel&gt; &amp; &lt;@U123&gt;");
  equal(chunks("🌌".repeat(9000)).join(""), "🌌".repeat(9000));
});
