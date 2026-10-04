# MLB v9 v4 regression fixture

These are byte-identical copies of the local August 31 research table and
manifest from `outputs/research/mlb_v9/{tables,manifests}/`, packaged so the
integrity regression runs in a fresh checkout without ignored research output.
The parquet SHA-256 is
`afaef3d2a6645d8bfe922bd307b2a9fec0f83f7d9bcacb0572e58fba44be4e1d`.

The test recomputes the file hash, row/feature counts and collinearity audit.
This is a historical regression fixture. It does not certify point-in-time
availability, establish model improvement, or authorize promotion. The source
research artifacts remain unchanged.
