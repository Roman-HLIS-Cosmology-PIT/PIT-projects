import { DefineDatastore, Schema } from "deno-slack-sdk/mod.ts";

// GitHub is the sole writer of project metadata. Slack functions only read it.
export const Projects = DefineDatastore({
  name: "pit_projects",
  primary_key: "id",
  attributes: {
    id: { type: Schema.types.string },
    payload: { type: Schema.types.string },
  },
});
// Immutable events: simultaneous voters never overwrite another person's vote.
export const Ballots = DefineDatastore({
  name: "pit_ballots",
  primary_key: "id",
  attributes: {
    id: { type: Schema.types.string },
    poll: { type: Schema.types.string },
    revision: { type: Schema.types.string },
    actor: { type: Schema.types.string },
    stamp: { type: Schema.types.string },
    action: { type: Schema.types.string },
  },
});
// Slack owns message locations and delivery receipts, never the decision state.
export const Messages = DefineDatastore({
  name: "pit_messages",
  primary_key: "id",
  attributes: {
    id: { type: Schema.types.string },
    channel: { type: Schema.types.string },
    ts: { type: Schema.types.string },
  },
});
