import { DefineWorkflow } from "deno-slack-sdk/mod.ts";
import { Sync } from "./functions/sync.ts";
import { Vote } from "./functions/vote.ts";
import { Command } from "./functions/command.ts";

export const SyncWorkflow = DefineWorkflow({
  callback_id: "pit_sync_workflow",
  title: "Synchronize PIT project",
  input_parameters: Sync.definition.input_parameters,
});
SyncWorkflow.addStep(Sync, {
  poll: SyncWorkflow.inputs.poll,
  delivery: SyncWorkflow.inputs.delivery,
});

export const VoteWorkflow = DefineWorkflow({
  callback_id: "pit_vote_workflow",
  title: "Vote on PIT project",
  input_parameters: Vote.definition.input_parameters,
});
VoteWorkflow.addStep(Vote, {
  poll: VoteWorkflow.inputs.poll,
  revision: VoteWorkflow.inputs.revision,
  action: VoteWorkflow.inputs.action,
  channel: VoteWorkflow.inputs.channel,
  user: VoteWorkflow.inputs.user,
  message: VoteWorkflow.inputs.message,
  stamp: VoteWorkflow.inputs.stamp,
  interactivity: VoteWorkflow.inputs.interactivity,
});

export const CommandWorkflow = DefineWorkflow({
  callback_id: "pit_command_workflow",
  title: "Override PIT vote",
  input_parameters: Command.definition.input_parameters,
});
CommandWorkflow.addStep(Command, {
  channel: CommandWorkflow.inputs.channel,
  user: CommandWorkflow.inputs.user,
  thread: CommandWorkflow.inputs.thread,
  message: CommandWorkflow.inputs.message,
  text: CommandWorkflow.inputs.text,
});
