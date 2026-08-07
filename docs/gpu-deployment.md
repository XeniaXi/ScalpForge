# Alibaba GPU training boundary

The rented GPU should be treated as an isolated research worker, not as the trading system. Market ingestion, replay, the risk governor, and paper execution remain operational services and must keep working without a GPU.

## Intended flow

1. The local/data environment produces a content-addressed dataset manifest and immutable Parquet shards.
2. Approved datasets are copied to Alibaba Object Storage Service (OSS) or a private mounted volume.
3. A GPU job receives only a dataset URI, experiment configuration, and a short-lived artifact credential.
4. Training emits a signed model bundle containing weights, feature schema, calibration data, metrics, code revision, and dataset ID.
5. A separate promotion step verifies the bundle in replay. A model cannot promote itself.

## Isolation rules

- Never put MT4, broker, or execution credentials on the GPU host.
- Use a dedicated VPC/security group with inbound access denied by default.
- Use RAM roles or short-lived credentials instead of static access keys.
- Encrypt disks and OSS buckets; keep datasets private and define retention limits.
- Pin CUDA, driver, framework, and container versions in the experiment record.
- Stop or release the instance when idle and persist artifacts outside the instance disk.
- Treat downloaded base images and model dependencies as supply-chain inputs requiring hashes/scans.

## Workload split

GPU candidates: transformer/news embeddings, representation learning, large hyperparameter batches, and calibrated ensemble training.

CPU candidates: tick validation, deterministic replay, causal reaction windows, most feature computation, risk tests, API services, and paper execution.

## Before provisioning

Choose the exact Alibaba region, instance/GPU family, operating system, CUDA version, storage size, expected training hours, and whether data may legally be stored in that region. Then add a pinned training image and Terraform/Alibaba ROS definition rather than configuring the server manually.
