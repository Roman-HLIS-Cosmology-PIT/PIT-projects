import type { Trigger } from "deno-slack-api/types.ts";
import { CommandWorkflow } from "../workflows.ts";
import { CHANNEL_ID } from "../channel.ts";

const trigger: Trigger<typeof CommandWorkflow.definition> = {
  type: "event",
  name: "PIT thread override",
  workflow: "#/workflows/pit_command_workflow",
  event: {
    event_type: "slack#/events/message_posted",
    channel_ids: [CHANNEL_ID],
    filter: {
      version: 1,
      root: {
        operator: "OR",
        inputs: [
          { statement: "{{data.text}} == '!force-accept'" },
          { statement: "{{data.text}} == '!force-refuse'" },
        ],
      },
    },
  },
  inputs: {
    channel: { value: "{{data.channel_id}}" },
    user: { value: "{{data.user_id}}" },
    thread: { value: "{{data.thread_ts}}" },
    message: { value: "{{data.message_ts}}" },
    text: { value: "{{data.text}}" },
  },
};
export default trigger;
