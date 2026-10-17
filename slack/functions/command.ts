import { DefineFunction, Schema, SlackFunction } from "deno-slack-sdk/mod.ts";
import { Ballots, Messages } from "../datastores.ts";
import { CHANNEL_ID } from "../channel.ts";
import {
  checked,
  digest,
  project,
  query,
  update,
  verifyChannel,
} from "./runtime.ts";

export const Command = DefineFunction({
  callback_id: "pit_command",
  title: "Request project override",
  source_file: "functions/command.ts",
  input_parameters: {
    properties: {
      channel: { type: Schema.slack.types.channel_id },
      user: { type: Schema.slack.types.user_id },
      thread: { type: Schema.types.string },
      message: { type: Schema.types.string },
      text: { type: Schema.types.string },
    },
    required: ["channel", "user", "message", "text"],
  },
});
export default SlackFunction(Command, async ({ inputs, client, env }) => {
  try {
    if (
      inputs.channel !== CHANNEL_ID || !inputs.thread ||
      !/^!force-(accept|refuse)$/.test(inputs.text.trim())
    ) return { outputs: {} };
    await verifyChannel(client);
    const locations = await query(client, Messages.name);
    const location = locations.find((m) =>
      /^pr-[0-9]+$/.test(m.id) &&
      m.channel === CHANNEL_ID && m.ts === inputs.thread
    );
    if (!location) return { outputs: {} };
    const current = await project(client, location.id);
    if (!["waiting", "processing"].includes(current.status)) {
      return { outputs: {} };
    }
    if (!env.GITHUB_DISPATCH_TOKEN) {
      throw new Error("Set GITHUB_DISPATCH_TOKEN for immediate checks");
    }
    const id = await digest(`${inputs.channel}:${inputs.message}`);
    // An edited/deleted Slack command is not a vote retraction. The immutable
    // request remains auditable and retries reuse the same event ID.
    const old = checked(
      await client.apps.datastore.get({ datastore: Ballots.name, id }),
    );
    if (!old.item?.id) {
      checked(
        await client.apps.datastore.put({
          datastore: Ballots.name,
          item: {
            id,
            poll: current.id,
            revision: current.revision,
            actor: inputs.user,
            stamp: inputs.message,
            action: `force:${inputs.text.trim().slice(7)}`,
          },
        }),
      );
    }
    const response = await fetch(
      `https://api.github.com/repos/${current.repo}/actions/workflows/slack.yml/dispatches`,
      {
        method: "POST",
        redirect: "error",
        headers: {
          Authorization: `Bearer ${env.GITHUB_DISPATCH_TOKEN}`,
          Accept: "application/vnd.github+json",
          "Content-Type": "application/json",
          "X-GitHub-Api-Version": "2022-11-28",
        },
        body: JSON.stringify({
          ref: "main",
          inputs: { operation: "check", pr_number: String(current.pr) },
        }),
      },
    );
    if (response.status !== 204) {
      throw new Error(
        `GitHub dispatch failed (${response.status}); request retained for next check`,
      );
    }
    await update(client, env, await project(client, current.id));
    return { outputs: {} };
  } catch (error) {
    return { error: String(error) };
  }
});
