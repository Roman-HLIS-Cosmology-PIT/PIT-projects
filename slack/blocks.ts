import { type Ballot, chunks, escape, type Project, tally } from "./logic.ts";
import { TEST_BUTTONS } from "./channel.ts";

const section = (text: string) => ({
  type: "section",
  text: { type: "mrkdwn", text },
});
export function snapshot(project: Project) {
  // Escape submitted text: no @channel, user mentions or attacker-controlled links.
  return [
    section(
      `*${
        project.kind === "EP" ? "New EP" : "CPP claim"
      }* · <https://github.com/${project.repo}/pull/${project.pr}|Review PR #${project.pr}>`,
    ),
    ...[
      ["Project title", project.title],
      ["Working Group", project.groups],
      ["Lead(s)", project.leads],
      ["Other active contributors", project.contributors],
      ["Project description", project.description],
    ].flatMap(([label, value]) =>
      chunks(escape(value)).map((part, i) =>
        section(`${i === 0 ? `*${label}:*\n` : ""}${part}`)
      )
    ),
  ];
}
export function blocks(project: Project, events: Ballot[], url: string) {
  const count = tally(project, events);
  const status = {
    waiting: "🟠 Waiting",
    processing: "🟠 Waiting · Applying decision",
    accepted: "🟢 Accepted",
    refused: "🔴 Refused",
  }[project.status];
  const button = (action: string, label: string) => ({
    type: "workflow_button",
    text: { type: "plain_text", text: label },
    action_id: action,
    workflow: {
      trigger: {
        url,
        customizable_input_parameters: [
          { name: "poll", value: project.id },
          { name: "revision", value: project.revision },
          { name: "action", value: action },
        ],
      },
    },
  });
  // Slack caps a message at 50 blocks. The full snapshot is always delivered
  // in the thread; root fields are shortened only for exceptionally long text.
  const shorten = (text: string) =>
    Array.from(text).length > 6000
      ? Array.from(text).slice(0, 6000).join("") +
        "\n[Continued in the project information thread]"
      : text;
  const preview = {
    ...project,
    groups: shorten(project.groups),
    leads: shorten(project.leads),
    contributors: shorten(project.contributors),
    description: shorten(project.description),
  };
  const result = [
    section(`*${status}*`),
    ...snapshot(preview),
    section(
      `*Votes:* Accept ${count.accept} · Refuse ${count.refuse} · Abstain ${count.abstain}\n` +
        `*Participation:* ${count.participation}/${project.members} · *Quorum:* ${project.quorum}\n` +
        (count.simulation
          ? "*TEST: synthetic quorum; the next real vote restores real ballots.*\n"
          : "") +
        (count.force ? `*Override requested:* ${count.force}\n` : "") +
        "Abstentions count toward quorum. Ties and abstention-only votes remain waiting.",
    ),
  ];
  if (project.note) result.push(section(escape(project.note)));
  if (project.status === "waiting") {
    result.push(
      {
        type: "actions",
        elements: [
          button("vote:accept", "Accept"),
          button("vote:refuse", "Refuse"),
          button("vote:abstain", "Abstain"),
          button("refresh", "Refresh"),
        ],
      } as unknown as typeof result[number],
    );
    if (TEST_BUTTONS && project.test_buttons) {
      result.push(
        {
          type: "actions",
          elements: [
            button("test:accept", "TEST: Accept quorum"),
            button("test:refuse", "TEST: Refuse quorum"),
            button("test:abstain", "TEST: Abstain quorum"),
          ],
        } as unknown as typeof result[number],
      );
    }
  }
  return result;
}
