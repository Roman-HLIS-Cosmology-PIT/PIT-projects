import type { SlackAPIClient } from "deno-slack-api/types.ts";
import { CHANNEL_ID, CHANNEL_NAME, TEST_BUTTONS } from "../channel.ts";
import { Ballots, Messages, Projects } from "../datastores.ts";
import { blocks, snapshot } from "../blocks.ts";
import type { Ballot, Project } from "../logic.ts";

export function checked<T extends { ok: boolean; error?: string }>(
  result: T,
): T {
  if (!result.ok) {
    throw new Error(`Slack API failed: ${result.error ?? "unknown"}`);
  }
  return result;
}
export async function project(
  client: SlackAPIClient,
  id: string,
): Promise<Project> {
  if (!/^pr-[1-9][0-9]*$/.test(id)) throw new Error("Invalid poll ID");
  const result = checked(
    await client.apps.datastore.get({ datastore: Projects.name, id }),
  );
  const value: Project = JSON.parse(result.item.payload);
  if (value.id !== id || value.channel !== CHANNEL_ID) {
    throw new Error("Wrong channel binding");
  }
  return value;
}
export async function query(
  client: SlackAPIClient,
  datastore: string,
  poll?: string,
) {
  const items: Record<string, string>[] = [];
  let cursor: string | undefined;
  do {
    const result = checked(
      await client.apps.datastore.query({
        datastore,
        cursor,
        ...(poll
          ? {
            expression: "#p = :p",
            expression_attributes: { "#p": "poll" },
            expression_values: { ":p": poll },
          }
          : {}),
      }),
    );
    items.push(...result.items);
    cursor = result.next_cursor || result.response_metadata?.next_cursor ||
      undefined;
  } while (cursor);
  return items;
}
export async function anchor(client: SlackAPIClient, id: string) {
  const result = checked(
    await client.apps.datastore.get({ datastore: Messages.name, id }),
  );
  return result.item?.ts ? result.item : undefined;
}
export async function verifyChannel(client: SlackAPIClient, test = false) {
  const info =
    checked(await client.conversations.info({ channel: CHANNEL_ID })).channel;
  if (
    !info?.is_private || info.is_shared || info.is_ext_shared ||
    info.is_org_shared
  ) {
    throw new Error("Voting requires a private, unshared channel");
  }
  if (test && (!TEST_BUTTONS || info.name !== CHANNEL_NAME)) {
    throw new Error("Test controls are restricted to test-pit-projects");
  }
}
export async function update(
  client: SlackAPIClient,
  env: Record<string, string>,
  value: Project,
  attempt = 0,
) {
  const location = await anchor(client, value.id);
  if (!location) throw new Error("Poll has no original message");
  if (location.channel !== CHANNEL_ID) throw new Error("Wrong message channel");
  const events = await query(client, Ballots.name, value.id) as Ballot[];
  checked(
    await client.chat.update({
      channel: CHANNEL_ID,
      ts: location.ts,
      text: `PIT project #${value.pr}: ${value.status}`,
      blocks: blocks(value, events, env.VOTE_TRIGGER_URL),
      unfurl_links: false,
      unfurl_media: false,
    }),
  );
  // A vote render can overlap a GitHub terminal update. Repair the display if
  // metadata advanced while chat.update was in flight.
  const latest = await project(client, value.id);
  if (JSON.stringify(latest) !== JSON.stringify(value)) {
    if (attempt >= 3) {
      throw new Error("Project kept changing; refresh its message");
    }
    await update(client, env, latest, attempt + 1);
  }
}
export async function digest(text: string) {
  const bytes = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(text),
  );
  return Array.from(new Uint8Array(bytes)).map((b) =>
    b.toString(16).padStart(2, "0")
  ).join("");
}
// Serialized delivery is invoked only by the single GitHub concurrency group.
// A receipt is reserved BEFORE sending. Ambiguous delivery is never reposted
// automatically: an operator recovers its timestamp (see README).
export async function deliver(
  client: SlackAPIClient,
  env: Record<string, string>,
  value: Project,
) {
  await verifyChannel(client);
  let location = await anchor(client, value.id);
  if (!location) {
    const receipt = checked(
      await client.apps.datastore.get({
        datastore: Messages.name,
        id: value.id,
      }),
    );
    if (receipt.item?.channel) {
      throw new Error(
        "Uncertain root delivery; repair its receipt before retrying",
      );
    }
    checked(
      await client.apps.datastore.put({
        datastore: Messages.name,
        item: { id: value.id, channel: CHANNEL_ID, ts: "" },
      }),
    );
    const result = checked(
      await client.chat.postMessage({
        channel: CHANNEL_ID,
        text: `PIT project #${value.pr}: ${value.status}`,
        blocks: blocks(value, [], env.VOTE_TRIGGER_URL),
        unfurl_links: false,
        unfurl_media: false,
      }),
    );
    checked(
      await client.apps.datastore.put({
        datastore: Messages.name,
        item: { id: value.id, channel: CHANNEL_ID, ts: result.ts! },
      }),
    );
    location = { channel: CHANNEL_ID, ts: result.ts! };
  }
  if (value.status === "waiting") {
    const id = `${value.id}:${value.revision}`;
    const receipt = checked(
      await client.apps.datastore.get({ datastore: Messages.name, id }),
    );
    if (!receipt.item?.channel) {
      checked(
        await client.apps.datastore.put({
          datastore: Messages.name,
          item: { id, channel: CHANNEL_ID, ts: "" },
        }),
      );
      // A project description can exceed Slack's 50-block message limit.
      const parts = snapshot(value);
      for (let i = 0; i < parts.length; i += 45) {
        checked(
          await client.chat.postMessage({
            channel: CHANNEL_ID,
            thread_ts: location.ts,
            text: `Validated project information ${
              value.revision.slice(0, 12)
            }`,
            blocks: parts.slice(i, i + 45),
            unfurl_links: false,
            unfurl_media: false,
          }),
        );
      }
      checked(
        await client.apps.datastore.put({
          datastore: Messages.name,
          item: { id, channel: CHANNEL_ID, ts: location.ts },
        }),
      );
    } else if (!receipt.item.ts) {
      throw new Error(
        "Uncertain revision delivery; repair receipt before retrying",
      );
    }
  }
  await update(client, env, value);
  if (value.delivery) {
    checked(
      await client.apps.datastore.put({
        datastore: Messages.name,
        item: { id: `ack:${value.delivery}`, channel: CHANNEL_ID, ts: "done" },
      }),
    );
  }
}
