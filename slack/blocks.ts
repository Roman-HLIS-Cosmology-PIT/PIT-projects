import { type Ballot, chunks, escape, type Project, tally } from "./logic.ts";
import { TEST_BUTTONS } from "./channel.ts";

const section = (text: string) => ({
  type: "section",
  text: { type: "mrkdwn", text },
});
function information(project: Project) {
  const field = (label: string, value: string) =>
    `*${label}:* ${escape(value).trim().replace(/\n\s*\n/g, "\n")}`;
  return [
    `*${project.kind === "EP" ? "New EP" : "CPP claim"}*`,
    field("Project title", project.title),
    field("Working Group", project.groups),
    field("Lead(s)", project.leads),
    field("Other active contributors", project.contributors),
    field("Project description", project.description),
    `*PR:* <https://github.com/${project.repo}/pull/${project.pr}|Review PR #${project.pr}>`,
  ].join("\n");
}
export function snapshot(project: Project) {
  return chunks(information(project)).map(section);
}
export function blocks(project: Project, events: Ballot[], legacyUrl?: string) {
  const count = tally(project, events);
  const status = {
    waiting: "🟠 Waiting",
    processing: "🟠 Waiting · Applying decision",
    accepted: "🟢 Accepted",
    refused: "🔴 Refused",
  }[project.status];
  const button = (action: string, label: string) => ({
    type: legacyUrl ? "workflow_button" : "button",
    text: { type: "plain_text", text: label },
    action_id: action,
    ...(legacyUrl
      ? {
        workflow: {
          trigger: {
            url: legacyUrl,
            customizable_input_parameters: [
              { name: "poll", value: project.id },
              { name: "revision", value: project.revision },
              { name: "action", value: action },
            ],
          },
        },
      }
      : {
        value: JSON.stringify({ poll: project.id, revision: project.revision }),
      }),
  });
  // Keep the first notification entirely in the channel. Exceptionally long
  // fields have an explicit PR continuation, never an automatic initial reply.
  const shorten = (text: string, limit: number) => {
    let length = 0;
    let result = "";
    for (const character of text) {
      length += escape(character).length;
      if (length > limit) return result + "… [Full text in the PR]";
      result += character;
    }
    return result;
  };
  const preview = {
    ...project,
    title: shorten(project.title, 500),
    groups: shorten(project.groups, 1000),
    leads: shorten(project.leads, 1000),
    contributors: shorten(project.contributors, 1000),
    description: shorten(project.description, 12000),
  };
  const text = `*${status}*\n${information(preview)}\n\n` +
    `*Votes:* Accept ${count.accept} · Refuse ${count.refuse} · Abstain ${count.abstain}\n` +
    `*Participation:* ${count.participation}/${project.members} · *Quorum:* ${project.quorum}\n` +
    (count.simulation
      ? "*TEST: synthetic quorum; the next real vote restores real ballots.*\n"
      : "") +
    (count.force ? `*Override requested:* ${count.force}\n` : "") +
    "Abstentions count toward quorum. Ties and abstention-only votes remain waiting." +
    (project.note ? `\n${escape(project.note)}` : "");
  // Short projects use one text block: Slack adds no gaps between their fields.
  const result = chunks(text).map(section);
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
