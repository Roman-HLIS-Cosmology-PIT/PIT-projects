import { DefineFunction, Schema, SlackFunction } from "deno-slack-sdk/mod.ts";
import { anchor, checked, deliver, project } from "./runtime.ts";

import { castVote } from "./vote.ts";
import { CHANNEL_ID } from "../channel.ts";

export const Sync = DefineFunction({
  callback_id: "pit_sync",
  title: "Synchronize project",
  source_file: "functions/sync.ts",
  input_parameters: {
    properties: {
      poll: { type: Schema.types.string },
      delivery: { type: Schema.types.string },
    },
    required: ["poll", "delivery"],
  },
});
export default SlackFunction(Sync, async ({ inputs, client, env, event }) => {
  try {
    const value = await project(client, inputs.poll);
    if (value.delivery !== inputs.delivery) {
      throw new Error("Stale synchronization request");
    }
    const pending = await deliver(
      client,
      env,
      value,
      event.function_execution_id,
    );
    // One execution owns the root's ordinary buttons for the entire review.
    // GitHub waits on the datastore acknowledgement, not workflow completion.
    if (pending) return { completed: false };
    return { outputs: {} };
  } catch (error) {
    // If a later delivery step failed after posting, keep its buttons alive.
    // No acknowledgement is written: GitHub reports failure and can retry.
    try {
      const owner = await anchor(client, `interactive:${inputs.poll}`);
      if (owner?.ts === event.function_execution_id) {
        console.error(String(error));
        return { completed: false };
      }
    } catch { /* Preserve the original delivery error. */ }
    return { error: String(error) };
  }
})
  .addBlockActionsHandler(
    /^(vote|test):(accept|refuse|abstain)$|^refresh$/,
    async ({ action, body, client, env }) => {
      let result: { error?: string };
      try {
        const selection = JSON.parse(action.value ?? "{}");
        if (selection.poll !== body.function_data.inputs.poll) {
          throw new Error("Wrong poll interaction");
        }
        const owner = await anchor(client, `interactive:${selection.poll}`);
        if (owner?.ts !== body.function_data.execution_id) {
          throw new Error("Use the current project message");
        }
        result = await castVote(
          {
            poll: selection.poll,
            revision: selection.revision,
            action: action.action_id,
            channel: body.container.channel_id ?? "",
            message: body.container.message_ts ?? "",
            user: body.user.id,
            stamp: action.action_ts,
            interactivity: body.interactivity,
          },
          client,
          env,
        );
      } catch (error) {
        result = { error: String(error) };
      }
      // Rejections are visible only to the person clicking; they do not add replies.
      if (result.error && body.container.channel_id === CHANNEL_ID) {
        checked(
          await client.chat.postEphemeral({
            channel: CHANNEL_ID,
            user: body.user.id,
            text: result.error,
          }),
        );
      }
    },
  );
