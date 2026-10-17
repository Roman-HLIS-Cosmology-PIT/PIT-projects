import type { Trigger } from "deno-slack-api/types.ts";
import { VoteWorkflow } from "../workflows.ts";

const trigger: Trigger<typeof VoteWorkflow.definition> = {
  type: "shortcut",
  name: "Vote on PIT project",
  workflow: "#/workflows/pit_vote_workflow",
  inputs: {
    poll: { customizable: true },
    revision: { customizable: true },
    action: { customizable: true },
    channel: { value: "{{data.channel_id}}" },
    user: { value: "{{data.user_id}}" },
    message: { value: "{{data.message_ts}}" },
    interactivity: { value: "{{data.interactivity}}" },
    stamp: { value: "{{event_timestamp}}" },
  },
};
export default trigger;
