import { SlackAPI, SlackFunctionTester } from "deno-slack-sdk/mod.ts";
import vote, { Vote } from "../functions/vote.ts";
import command, { Command } from "../functions/command.ts";
import { deliver, update, verifyChannel } from "../functions/runtime.ts";
import { CHANNEL_ID, CHANNEL_NAME } from "../channel.ts";
import { blocks } from "../blocks.ts";
import type { Project } from "../logic.ts";

function assert(value: unknown, message = "Assertion failed"): asserts value {
  if (!value) throw new Error(message);
}
const value: Project = {
  id: "pr-42",
  repo: "example/projects",
  pr: 42,
  issue: 12,
  revision: "r1",
  head: "a".repeat(40),
  channel: CHANNEL_ID,
  kind: "EP",
  members: 5,
  quorum: 3,
  status: "waiting",
  title: "Test",
  groups: "SCM",
  leads: "Alice",
  contributors: "Bob",
  description: "Scientific goals",
  test_buttons: true,
};

// Exercise the actual SDK handlers through a fake Web API, without network or tokens.
async function fixture(
  run: (state: {
    stores: Map<string, Map<string, Record<string, string>>>;
    posts: Record<string, string>[];
    updates: Record<string, string>[];
    dispatches: string[];
    channel: { is_private: boolean; is_shared: boolean; name: string };
    onUpdate?: () => void;
  }) => Promise<void>,
) {
  const state = {
    stores: new Map<string, Map<string, Record<string, string>>>([
      [
        "pit_projects",
        new Map([[value.id, { id: value.id, payload: JSON.stringify(value) }]]),
      ],
      [
        "pit_messages",
        new Map([[value.id, {
          id: value.id,
          channel: CHANNEL_ID,
          ts: "1.000001",
        }]]),
      ],
      ["pit_ballots", new Map()],
    ]),
    posts: [] as Record<string, string>[],
    updates: [] as Record<string, string>[],
    dispatches: [] as string[],
    channel: { is_private: true, is_shared: false, name: CHANNEL_NAME },
    onUpdate: undefined as (() => void) | undefined,
  };
  const original = globalThis.fetch;
  globalThis.fetch = ((url: string | URL | Request, init?: RequestInit) => {
    if (String(url).startsWith("https://api.github.com/")) {
      state.dispatches.push(String(init?.body));
      return Promise.resolve(new Response(null, { status: 204 }));
    }
    const method = String(url).split("/").at(-1);
    const args = Object.fromEntries(new URLSearchParams(String(init?.body)));
    const store = state.stores.get(args.datastore);
    let response: Record<string, unknown> = { ok: true };
    if (method === "apps.datastore.get") {
      response.item = store?.get(args.id) ?? {};
    } else if (method === "apps.datastore.put") {
      const item = JSON.parse(args.item);
      assert(store);
      store.set(item.id, item);
      response.item = item;
    } else if (method === "apps.datastore.query") {
      const poll = args.expression_values
        ? JSON.parse(args.expression_values)[":p"]
        : undefined;
      response.items = [...(store?.values() ?? [])].filter((item) =>
        !poll || item.poll === poll
      );
    } else if (method === "conversations.info") {
      response.channel = state.channel;
    } else if (method === "chat.postMessage") {
      state.posts.push(args);
      response.ts = `${10 + state.posts.length}.000001`;
    } else if (method === "chat.update") {
      state.updates.push(args);
      state.onUpdate?.();
    } else throw new Error(`Unexpected API ${method}`);
    return Promise.resolve(Response.json(response));
  }) as typeof fetch;
  try {
    await run(state);
  } finally {
    globalThis.fetch = original;
  }
}

const voteContext = (changes = {}) =>
  SlackFunctionTester(Vote).createContext({
    inputs: {
      poll: value.id,
      revision: value.revision,
      action: "vote:accept",
      channel: CHANNEL_ID,
      message: "1.000001",
      user: "U123",
      stamp: "100.123456",
      interactivity: {
        interactivity_pointer: "request-1",
        interactor: { id: "U123", secret: "test" },
      },
      ...changes,
    },
    env: { VOTE_TRIGGER_URL: "https://slack.com/shortcuts/FtTEST/test" },
  });

Deno.test("actual vote handler rejects other channels, copied messages and stale revisions", async () => {
  await fixture(async (state) => {
    for (
      const changes of [{ channel: "C_OTHER" }, { message: "2.000001" }, {
        revision: "old",
      }]
    ) {
      const result = await vote(voteContext(changes));
      assert(result.error);
    }
    assert(state.stores.get("pit_ballots")?.size === 0);
  });
});
Deno.test("vote retries are idempotent and real SDK rendering updates original message", async () => {
  await fixture(async (state) => {
    assert(!(await vote(voteContext())).error);
    assert(!(await vote(voteContext())).error);
    assert(state.stores.get("pit_ballots")?.size === 1);
    assert(
      state.updates.length === 2 &&
        state.updates.every((m) => m.ts === "1.000001"),
    );
    assert(state.posts.length === 0);
  });
});
Deno.test("test button and shared/public channel restrictions", async () => {
  await fixture(async (state) => {
    state.channel.name = "other-channel";
    assert((await vote(voteContext({ action: "test:accept" }))).error);
    state.channel.is_private = false;
    let rejected = false;
    try {
      await verifyChannel(SlackAPI("test"));
    } catch {
      rejected = true;
    }
    assert(rejected && state.stores.get("pit_ballots")?.size === 0);
    state.channel.is_private = true;
    state.channel.is_shared = true;
    rejected = false;
    try {
      await verifyChannel(SlackAPI("test"));
    } catch {
      rejected = true;
    }
    assert(rejected);
  });
});
Deno.test("thread command records override then immediately dispatches GitHub", async () => {
  await fixture(async (state) => {
    const context = SlackFunctionTester(Command).createContext({
      inputs: {
        channel: CHANNEL_ID,
        user: "U123",
        thread: "1.000001",
        message: "100.000001",
        text: "!force-refuse",
      },
      env: {
        GITHUB_DISPATCH_TOKEN: "test",
        VOTE_TRIGGER_URL: "https://slack.com/shortcuts/FtTEST/test",
      },
    });
    assert(!(await command(context)).error);
    assert(state.dispatches.length === 1);
    assert(state.dispatches[0].includes('"pr_number":"42"'));
    const ballots = [...state.stores.get("pit_ballots")!.values()];
    assert(ballots.length === 1 && ballots[0].action === "force:refuse");
  });
});
Deno.test("delivery retries preserve root and revision messages", async () => {
  await fixture(async (state) => {
    state.stores.get("pit_messages")!.clear();
    const p = { ...value, delivery: "request" };
    state.stores.get("pit_projects")!.set(value.id, {
      id: value.id,
      payload: JSON.stringify(p),
    });
    const client = SlackAPI("test");
    const env = { VOTE_TRIGGER_URL: "https://slack.com/shortcuts/FtTEST/test" };
    await deliver(client, env, p);
    await deliver(client, env, p);
    assert(state.posts.length === 2); // root + one full information snapshot
    assert(state.stores.get("pit_messages")!.get("ack:request")?.ts === "done");
  });
});
Deno.test("in-flight vote render repairs a concurrent accepted status", async () => {
  await fixture(async (state) => {
    state.onUpdate = () => {
      state.stores.get("pit_projects")!.set(value.id, {
        id: value.id,
        payload: JSON.stringify({ ...value, status: "accepted" }),
      });
      state.onUpdate = undefined;
    };
    await update(SlackAPI("test"), {
      VOTE_TRIGGER_URL: "https://slack.com/shortcuts/FtTEST/test",
    }, value);
    assert(state.updates.length === 2);
    assert(state.updates[1].blocks.includes("🟢 Accepted"));
  });
});
Deno.test("very long project fields fit the root message block limits", () => {
  const result = blocks(
    { ...value, description: "<&🌌".repeat(30000) },
    [],
    "https://slack.com/shortcuts/FtTEST/test",
  );
  assert(result.length <= 50);
  for (const block of result) {
    if (block.text) assert(Array.from(block.text.text).length <= 3000);
  }
});
