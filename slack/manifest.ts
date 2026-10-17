import { Manifest } from "deno-slack-sdk/mod.ts";
import { CommandWorkflow, SyncWorkflow, VoteWorkflow } from "./workflows.ts";
import { Ballots, Messages, Projects } from "./datastores.ts";

export default Manifest({
  name: "PIT Projects",
  description: "Private SC project votes with GitHub decisions",
  icon: "assets/icon.png",
  workflows: [SyncWorkflow, VoteWorkflow, CommandWorkflow],
  datastores: [Projects, Ballots, Messages],
  outgoingDomains: ["api.github.com"],
  botScopes: [
    "commands",
    "chat:write",
    "datastore:read",
    "datastore:write",
    "groups:read",
    "groups:history",
  ],
});
