import type { Trigger } from "deno-slack-api/types.ts";
import { SyncWorkflow } from "../workflows.ts";

const trigger: Trigger<typeof SyncWorkflow.definition> = {
  type: "webhook",
  name: "GitHub project sync",
  workflow: "#/workflows/pit_sync_workflow",
  inputs: {
    poll: { value: "{{data.poll}}" },
    delivery: { value: "{{data.delivery}}" },
  },
};
export default trigger;
