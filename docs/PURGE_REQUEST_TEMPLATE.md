# GitHub cache-purge request template (2026-08-15 exposure)

Use this if you want GitHub to remove the cached views of the superseded
commits from the public repo. Go to https://support.github.com/contact
(or the "Contact GitHub Support" flow), category: Account → Security /
"Request removal of sensitive data in a public repository".

Subject: Purge request — superseded commits on Vc9002/model-prediction

Body:

> Repository: https://github.com/Vc9002/model-prediction
>
> On 2026-08-15, a force-push replaced the
> `cleanup/final-debug-2026-08-14` branch. The superseded commits briefly
> contained SQLite database files (ledger/prediction stores with personal
> operational data) that were pushed in error:
>
> - 9865921 fix: consolidation P0 — fail-closed runtime root, single
>   scheduler, launchd-owned dashboard
> - 5e51d72 fix: consolidation P1 — hydrate dashboard job history at
>   startup
> - 411b543 chore: consolidation K part 1 — rolling/frozen artifact
>   split + untrack runtime churn
> - 0eb2b29 fix: rebuild view falls back to read-only repo resolution in
>   env-less contexts
> - 0831fd2 docs: CLAUDE.md — K runtime-singularity contracts
> - 5d54a80 fix: K — frozen champions + dashboard cache db move to
>   runtime root; quarantine repo relics
> - caf20fc chore: N-prep — ruff import sorting + K acceptance record in
>   PROJECT_STATUS
>
> The database blobs live in commits 9865921 and 5d54a80
> (backups/split-brain-quarantine-20260815/*.db and the earlier
> backups/.../ledgers.db). The branch history was rewritten to remove
> them; the current branch head is 24098c1.
>
> Please purge cached views of these seven commits (commit pages,
> .patch/.diff endpoints, and any references) so the removed files are no
> longer publicly reachable.

Notes:

- GitHub usually removes cached views for dangling commits after a
  force-push on request; allow a few days.
- The repo's `origin/main` history already contains a tracked
  `data/production/predictions.db` from before this session (the repo's
  prior convention). If that is also a concern, mention it in the same
  ticket as a separate line item — it is a much larger purge scope.
