import { List } from "@raycast/api";

/** Skeleton — wired to real data in T-11. */
export default function RunWorkflow() {
  return (
    <List>
      <List.Item title="Record to Obsidian" subtitle="Audio recording, summary, vault save" />
      <List.Item title="Transcribe File" subtitle="Audio file → text" />
    </List>
  );
}
