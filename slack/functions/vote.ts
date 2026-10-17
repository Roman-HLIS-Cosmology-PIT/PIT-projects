import { DefineFunction, Schema, SlackFunction } from "deno-slack-sdk/mod.ts";
import { Ballots } from "../datastores.ts";
import { CHANNEL_ID } from "../channel.ts";
import {
  anchor,
  checked,
  digest,
  project,
  update,
  verifyChannel,
} from "./runtime.ts";

export const Vote = DefineFunction({
  callback_id: "pit_vote",
  title: "Vote on project",
  source_file: "functions/vote.ts",
  input_parameters: {
    properties: {
      poll: { type: Schema.types.string },
      revision: { type: Schema.types.string },
      action: { type: Schema.types.string },
      channel: { type: Schema.slack.types.channel_id },
      user: { type: Schema.slack.types.user_id },
      message: { type: Schema.types.string },
      stamp: { type: Schema.types.string },
      interactivity: { type: Schema.slack.types.interactivity },
    },
    required: [
      "poll",
      "revision",
      "action",
      "channel",
      "user",
      "message",
      "stamp",
      "interactivity",
    ],
  },
});
export async function castVote(
  inputs: {
    poll: string;
    revision: string;
    action: string;
    channel: string;
    user: string;
    message: string;
    stamp: string;
    interactivity: { interactivity_pointer: string };
  },
  client: import("deno-slack-api/types.ts").SlackAPIClient,
  env: Record<string, string>,
) {
  try {
    if (inputs.channel !== CHANNEL_ID) {
      throw new Error("Voting is restricted to the configured SC channel");
    }
    const current = await project(client, inputs.poll);
    const location = await anchor(client, current.id);
    if (
      location?.channel !== inputs.channel || location.ts !== inputs.message
    ) {
      throw new Error("Use the buttons on the original project message");
    }
    if (current.status !== "waiting" || current.revision !== inputs.revision) {
      throw new Error(
        "Proposal changed or voting closed; refresh the original message",
      );
    }
    if (inputs.action !== "refresh") {
      if (!/^(vote|test):(accept|refuse|abstain)$/.test(inputs.action)) {
        throw new Error("Invalid vote");
      }
      if (inputs.action.startsWith("test:") && !current.test_buttons) {
        throw new Error("Test controls disabled");
      }
      await verifyChannel(client, inputs.action.startsWith("test:"));
      // Slack's request is the idempotency key. Timestamp comes from the Slack
      // interactivity payload, not an editable workflow-button parameter.
      const pointer = inputs.interactivity.interactivity_pointer;
      const id = await digest(pointer);
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
              stamp: inputs.stamp,
              action: inputs.action,
            },
          }),
        );
      }
    }
    await update(client, env, await project(client, current.id));
    return { outputs: {} };
  } catch (error) {
    return { error: String(error) };
  }
}
// Compatibility for messages sent before direct buttons were deployed.
export default SlackFunction(
  Vote,
  ({ inputs, client, env }) => castVote(inputs, client, env),
);
