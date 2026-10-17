import { DefineFunction, Schema, SlackFunction } from "deno-slack-sdk/mod.ts";
import { deliver, project } from "./runtime.ts";

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
export default SlackFunction(Sync, async ({ inputs, client, env }) => {
  try {
    if (!env.VOTE_TRIGGER_URL) {
      throw new Error("Set VOTE_TRIGGER_URL before syncing");
    }
    const value = await project(client, inputs.poll);
    if (value.delivery !== inputs.delivery) {
      throw new Error("Stale synchronization request");
    }
    await deliver(client, env, value);
    return { outputs: {} };
  } catch (error) {
    return { error: String(error) };
  }
});
