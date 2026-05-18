# LB4 Multiplexer Debug Tool

A unified debugging tool for LB4 multiplexer services, designed to test P2P connections (Ethereum devp2p and libp2p) exposed through mux services in Kubernetes clusters.

## Quick Start

```bash
cd /home/revol/code/helm-charts/charts/lb4-multiplexer/tools

# List all mux services
./mux-debug list

# Show routing graph
./mux-debug graph cyber-mux

# Test a specific pod (with namespace auto-detection)
./mux-debug test pod cyber-mainnet/cyber-lb4-seq-full-archive-0-geth-0

# Or explicitly specify namespace
./mux-debug test pod cyber-lb4-seq-full-archive-0-geth-0 -n cyber-mainnet

# Test all routes in a mux
./mux-debug test mux cyber-mux
```

## Key Features

- **Namespace Auto-detection**: 支持 `namespace/name` 格式，可以直接复制粘贴输出中的资源名称
- **Intelligent Caching**: 自动缓存 Kubernetes 资源信息，显著提升性能（缓存有效期 60 秒）
- **P2P Protocol Verification**: 不仅测试 TCP 连接，还验证 P2P 握手和 peer ID 匹配
- **Multi-protocol Support**: 支持 Ethereum devp2p (geth) 和 libp2p (op-node)

## Commands

### 1. `list` - List All Mux Services

显示集群中所有的 mux services。

```bash
./mux-debug list [--context CONTEXT]
```

**示例:**
```bash
./mux-debug list --context mainnet
```

**输出:**
```
====================================================================================================
Mux Services (context: mainnet)
====================================================================================================

lb4/aztec-mux
  LoadBalancer: k8s-lb4-aztecmux-xxx.elb.us-west-2.amazonaws.com
  Channels: 1

lb4/cyber-mux
  LoadBalancer: k8s-lb4-cybermux-xxx.elb.us-west-2.amazonaws.com
  Channels: 6

...
```

---

### 2. `graph` - Show Routing Graph

显示一个 mux 的完整路由图,包括:
- LoadBalancer 地址
- 每个 channel 的路由
- 端口映射关系
- 目标 pods 及其状态

```bash
./mux-debug graph <mux> [--namespace NAMESPACE] [--context CONTEXT]
```

**示例:**
```bash
./mux-debug graph cyber-mux -n lb4
```

**输出:**
```
====================================================================================================
Mux Routing Graph: lb4/cyber-mux
====================================================================================================
LoadBalancer: k8s-lb4-cybermux-xxx.elb.us-west-2.amazonaws.com
Total Channels: 6
Total Routes: 12

┌─ Channel: cyber-mainnet/cyber-lb4-seq-full-archive-0-geth
│  ├── k8s-lb4-cybermux-xxx.elb.us-west-2.amazonaws.com:31200/TCP
│  │    ↓
│  │    Service Port: 31200
│  │    NodePort: 31200
│  │    ↓
│  │    Pods (1):
│  │      └── ✓ cyber-lb4-seq-full-archive-0-geth-0
│  │          IP: 10.0.1.100
│  │          Node: ip-10-0-1-50.us-west-2.compute.internal
│  │
│  └── k8s-lb4-cybermux-xxx.elb.us-west-2.amazonaws.com:31201/TCP
│       ↓
│       Service Port: 31201
│       NodePort: 31201
│       ↓
│       Pods (1):
│         └── ✓ cyber-lb4-seq-full-archive-0-geth-0
│             IP: 10.0.1.100
│             Node: ip-10-0-1-50.us-west-2.compute.internal

┌─ Channel: cyber-mainnet/cyber-lb4-seq-full-archive-0-node
...
```

**图例:**
- `✓` - Pod 处于 Ready 状态
- `⚠` - Pod 未 Ready

---

### 3. `test pod` - Test a Specific Pod

测试特定 pod 的连接,包括:
- 自动找到指向该 pod 的路由
- 测试 TCP 连接到 LoadBalancer
- 验证 P2P 协议握手（devp2p/libp2p）
- 验证 peer ID 匹配
- 获取并显示 P2P peer 信息 (enode/peer ID)
- 生成外部可用的连接字符串

```bash
# Using namespace/name format (recommended)
./mux-debug test pod <namespace>/<pod> [--context CONTEXT]

# Or explicitly specify namespace
./mux-debug test pod <pod> --namespace NAMESPACE [--context CONTEXT]
```

**示例:**
```bash
# Auto-detect namespace from resource name
./mux-debug test pod cyber-mainnet/cyber-lb4-seq-full-archive-0-geth-0

# Or specify namespace explicitly
./mux-debug test pod cyber-lb4-seq-full-archive-0-geth-0 -n cyber-mainnet
```

**输出:**
```
====================================================================================================
Pod Test: cyber-mainnet/cyber-lb4-seq-full-archive-0-geth-0
====================================================================================================
Mux: lb4/cyber-mux
Channel: cyber-mainnet/cyber-lb4-seq-full-archive-0-geth
Route: k8s-lb4-cybermux-xxx.elb.us-west-2.amazonaws.com:31200/TCP -> 31200

✓ TCP: TCP connection successful to k8s-lb4-cybermux-xxx.elb.us-west-2.amazonaws.com:31200

✓ P2P: devp2p port reachable (peer ID verified via RPC: 0123456789abcdef...)

P2P Protocol: devp2p
Enode: enode://abc123...@10.0.1.100:31200

ℹ External Enode:
  enode://abc123...@k8s-lb4-cybermux-xxx.elb.us-west-2.amazonaws.com:31200
```

**使用场景:**
- 验证某个特定 pod 是否可以从外部访问
- 获取用于配置其他节点的 enode/multiaddr
- 排查单个 pod 的连接问题

---

### 4. `test mux` - Test All Routes in a Mux

批量测试一个 mux 下的所有路由。

```bash
./mux-debug test mux <mux> [--namespace NAMESPACE] [--context CONTEXT]
```

**示例:**
```bash
./mux-debug test mux cyber-mux -n lb4
```

**输出:**
```
====================================================================================================
Mux Test: lb4/cyber-mux
====================================================================================================
LoadBalancer: k8s-lb4-cybermux-xxx.elb.us-west-2.amazonaws.com
Total Routes: 12

Channel: cyber-mainnet/cyber-lb4-seq-full-archive-0-geth
--------------------------------------------------------------------------------
✓ Port 31200/TCP: TCP connection successful
   Pods: 1/1 ready
✓ Port 31201/TCP: TCP connection successful
   Pods: 1/1 ready

Channel: cyber-mainnet/cyber-lb4-seq-full-archive-0-node
--------------------------------------------------------------------------------
✓ Port 31202/TCP: TCP connection successful
   Pods: 1/1 ready
...
```

**使用场景:**
- 快速验证整个 mux 的健康状态
- 发现哪些路由有问题
- CI/CD 中的自动化测试

---

### 5. `test channel` - Test a Channel Service

测试一个 channel service 的连接。

```bash
./mux-debug test channel <channel> --namespace NAMESPACE [--context CONTEXT]
```

**示例:**
```bash
./mux-debug test channel cyber-lb4-seq-full-archive-0-geth -n cyber-mainnet
```

**使用场景:**
- 验证 channel service 配置是否正确
- 测试 service 下的 pod 连接

---

### 6. `peer-info` - Get Peer Info from a Pod

获取 pod 的 P2P peer 信息,不进行连接测试。

```bash
# Using namespace/name format (recommended)
./mux-debug peer-info <namespace>/<pod> [--context CONTEXT]

# Or explicitly specify namespace
./mux-debug peer-info <pod> --namespace NAMESPACE [--context CONTEXT]
```

**示例:**
```bash
# For geth (with namespace auto-detection)
./mux-debug peer-info cyber-mainnet/cyber-lb4-seq-full-archive-0-geth-0

# For op-node (explicit namespace)
./mux-debug peer-info cyber-lb4-seq-full-archive-0-node-0 -n cyber-mainnet
```

**输出 (geth):**
```
====================================================================================================
Peer Info: cyber-mainnet/cyber-lb4-seq-full-archive-0-geth-0
====================================================================================================
Protocol: devp2p
Enode: enode://abc123...@10.0.1.100:31200
```

**输出 (op-node):**
```
====================================================================================================
Peer Info: cyber-mainnet/cyber-lb4-seq-full-archive-0-node-0
====================================================================================================
Protocol: libp2p
Peer ID: 16Uiu2HAm...
Multiaddr: /ip4/0.0.0.0/tcp/9222
```

**使用场景:**
- 快速获取 enode 或 peer ID
- 用于配置文件生成
- 脚本自动化

---

## 符号说明

- `✓` (SUCCESS): 测试通过
- `✗` (FAILURE): 测试失败
- `⚠` (WARNING): 警告
- `-` (SKIP): 已跳过
- `ℹ` (INFO): 信息

---

## Advanced Usage

### 使用不同的 Context

工具支持任意 kubectl context:

```bash
# Mainnet
./mux-debug list --context mainnet

# Testnet
./mux-debug list --context stg

# Local
./mux-debug list --context minikube
```

### Namespace Auto-detection

所有命令都支持 `namespace/name` 格式，方便直接复制粘贴输出中的资源名称:

```bash
# List 命令的输出包含 namespace/name 格式
./mux-debug list
# Output: lb4/cyber-mux, cyber-mainnet/cyber-lb4-seq-full-archive-0-geth, ...

# 可以直接复制粘贴到其他命令
./mux-debug graph lb4/cyber-mux
./mux-debug peer-info cyber-mainnet/cyber-lb4-seq-full-archive-0-geth-0
./mux-debug test pod cyber-mainnet/cyber-lb4-seq-full-archive-0-geth-0
```

### Caching

工具会自动缓存 Kubernetes 资源查询结果，显著提升性能:

- **Cache Directory**: `/tmp/mux-debug-cache/`
- **Cache TTL**: 60 seconds
- **Cached Resources**: Services, Endpoints, Pods

缓存对频繁查询同一集群特别有用，例如连续测试多个 pods 时。

### Verbose 模式

添加 `-v` 查看详细调试日志（包括缓存命中信息）:

```bash
./mux-debug -v graph cyber-mux
```

### 批量测试脚本

创建脚本批量测试所有 mux:

```bash
#!/bin/bash
CONTEXT="mainnet"
MUX_LIST=$(./mux-debug list --context "$CONTEXT" | grep "/" | awk '{print $1}')

for mux in $MUX_LIST; do
    mux_name=$(basename "$mux")
    mux_ns=$(dirname "$mux")
    echo "Testing $mux..."
    ./mux-debug test mux "$mux_name" -n "$mux_ns" --context "$CONTEXT"
done
```

---

## 建议的额外功能

以下是一些可能有用的额外功能建议:

### 1. `watch` - 持续监控模式

持续监控 mux 的状态变化:

```bash
./mux-debug watch mux cyber-mux -n lb4 --interval 10
```

**用途:**
- 实时监控连接状态
- 发现间歇性问题
- 观察 pod 滚动更新时的影响

### 2. `compare` - 比较配置

比较不同集群或不同时间点的 mux 配置:

```bash
./mux-debug compare --mux cyber-mux --contexts mainnet,stg
```

**用途:**
- 验证配置一致性
- 发现配置漂移
- 审计变更

### 3. `export` - 导出配置

导出 mux 的配置为 JSON/YAML:

```bash
./mux-debug export cyber-mux -n lb4 --format json > cyber-mux.json
```

**用途:**
- 文档化当前配置
- 配置备份
- 与其他工具集成

### 4. `validate` - 配置验证

验证 channel service 配置是否符合最佳实践:

```bash
./mux-debug validate channel cyber-lb4-seq-full-archive-0-geth -n cyber-mainnet
```

**检查项:**
- 端口是否正确命名
- NodePort 是否已分配
- LoadBalancer 状态是否正常
- Endpoints 是否存在
- 安全组配置建议

### 5. `benchmark` - 性能测试

测试 LoadBalancer 的性能指标:

```bash
./mux-debug benchmark mux cyber-mux -n lb4 --duration 60s
```

**测试内容:**
- 连接延迟
- 连接成功率
- DNS 解析时间
- 并发连接能力

### 6. `trace` - 路由追踪

显示完整的数据包路径:

```bash
./mux-debug trace --from external --to pod cyber-lb4-seq-full-archive-0-geth-0 -n cyber-mainnet
```

**显示:**
```
External Client
  ↓
LoadBalancer (k8s-lb4-cybermux-xxx.elb.us-west-2.amazonaws.com:31200)
  ↓
Node (ip-10-0-1-50.us-west-2.compute.internal:31200)
  ↓
Pod (cyber-lb4-seq-full-archive-0-geth-0:31200)
```

### 7. `health` - 健康检查

全面的健康检查,生成报告:

```bash
./mux-debug health mux cyber-mux -n lb4 --output report.html
```

**检查项:**
- LoadBalancer 状态
- 所有路由的连接性
- Pod 健康状态
- Endpoints 同步状态
- 配置一致性
- P2P 连接可用性

### 8. `history` - 变更历史

查看 mux 的变更历史:

```bash
./mux-debug history mux cyber-mux -n lb4 --limit 10
```

**显示:**
- 端口添加/删除
- Channel 添加/删除
- Pod 变更
- LoadBalancer 地址变更

### 9. `suggest` - 智能建议

基于当前配置提供优化建议:

```bash
./mux-debug suggest mux cyber-mux -n lb4
```

**建议内容:**
- 端口冲突检测
- 资源优化建议
- 安全配置建议
- 最佳实践检查

### 10. `debug` - 深度调试

收集所有诊断信息:

```bash
./mux-debug debug mux cyber-mux -n lb4 --output debug-bundle.tar.gz
```

**包含内容:**
- Mux service YAML
- 所有 channel services YAML
- Endpoints 状态
- Pod 日志
- Events
- 连接测试结果
- P2P peer 信息

---

## 故障排查

### 常见问题

#### 1. "No route found for pod"

**原因:**
- Pod 不属于任何 channel service
- Channel service 未使用 lb4-multiplexer

**解决:**
```bash
# 检查 pod 的 labels
kubectl get pod <pod> -n <namespace> --show-labels

# 查找对应的 service
kubectl get svc -n <namespace> --selector app=<label>
```

#### 2. "TCP connection failed"

**原因:**
- LoadBalancer 未就绪
- 安全组/防火墙阻止
- NodePort 未分配
- Pod 未 Ready

**解决:**
```bash
# 检查 LoadBalancer 状态
kubectl get svc -n <namespace> <mux>

# 查看 service events
kubectl describe svc -n <namespace> <mux>

# 测试从集群内部访问
kubectl run -it --rm debug --image=busybox --restart=Never -- telnet <node-ip> <nodeport>
```

#### 3. "Could not get peer info"

**原因:**
- Geth/op-node 未启动
- RPC 端口未开启
- 权限不足

**解决:**
```bash
# 检查 pod 日志
kubectl logs -n <namespace> <pod> --tail 100

# 检查 RPC 是否可访问
kubectl exec -n <namespace> <pod> -- curl -s http://localhost:8545 -X POST \
  -H "Content-Type: application/json" \
  --data '{"jsonrpc":"2.0","method":"web3_clientVersion","params":[],"id":1}'
```

---

## 技术细节

### P2P 协议支持

#### Ethereum devp2p (geth)
- **API**: `admin.nodeInfo`
- **端口**: 默认 8545 (HTTP RPC)
- **输出**: enode URL
- **格式**: `enode://PUBKEY@IP:PORT`

#### libp2p (op-node)
- **API**: `opp2p_self`
- **端口**: 9545, 7545, 8545 (尝试顺序)
- **输出**: Peer ID + Multiaddr
- **格式**: `/dns4/HOST/tcp/PORT/p2p/PEER_ID`

### 路由查找算法

1. 列出所有 mux services
2. 对每个 mux,获取其 channels
3. 对每个 channel,获取其 endpoints
4. 从 endpoints 提取 pod 列表
5. 匹配目标 pod

### 连接测试方法

- **TCP**: 使用 Python socket 进行 TCP connect 测试
- **超时**: 默认 5 秒,批量测试时 3 秒
- **RPC 调用适配**: 自动尝试 `curl`，如果不可用则 fallback 到 `wget`
- **External DNS 处理**: 当 annotation 包含多个逗号分隔的 DNS 条目时，自动使用第一个条目
- **P2P Protocol Verification**:
  - **devp2p (geth)** - 三层验证:
    1. **完整 RLPx 握手** (优先): 如果 `rlpx-verify-helper` 可用，使用 Go + geth 原生库进行完整的 ECIES 加密握手
    2. **Python 简化握手** (降级): 如果安装了 `eth-keys`，尝试简化的 RLPx 握手（无 ECIES 加密，通常会超时但不影响调试）
    3. **TCP + RPC 测试** (基础): TCP 连接测试 + RPC peer ID 验证
    - RPC 方法: `admin_nodeInfo` (via geth attach 或 HTTP RPC)
  - **libp2p (op-node)**:
    - 发送 multistream-select 协议协商消息 (`/multistream/1.0.0\n`)
    - 验证响应包含正确的协议标识
    - 通过 RPC 获取的 peer ID 进行匹配验证
    - RPC 方法: `opp2p_self` (端口: 8545, 9545, 7545)
  - 所有 P2P 测试都会验证 peer ID 是否与预期匹配

**RLPx 握手实现 (devp2p):**

- **完整握手** (推荐): 使用 Go helper 工具 `rlpx-verify-helper`，基于 `go-ethereum/p2p/rlpx` 包
  - ✅ 完整的 ECIES 加密/解密
  - ✅ 正确的密钥交换协议
  - ✅ Peer ID 验证
  - ✅ 真实节点握手成功
- **Python 简化握手**: 使用 `eth-keys` 库的简化实现
  - ⚠️ 缺少 ECIES 加密，真实节点会拒绝握手
  - ⚠️ 主要用于测试连接性，不代表节点故障
- **基础测试**: TCP 连接 + RPC peer ID 获取
  - ✅ 足够用于大多数调试场景
  - ✅ 无额外依赖

### Performance Optimization

- **Caching**: 使用 pickle 缓存 kubectl 查询结果，TTL 60 秒
- **Cache Location**: `/tmp/mux-debug-cache/`
- **Cache Key Format**: `{context}_{resource_type}_ns_{namespace}_sel_{selector}.cache`
- 缓存可显著提升重复查询性能（约 10-20% 性能提升）

---

## Development

### 文件结构

```
tools/
├── README.md                      # 本文档
├── mux-debug                      # 统一调试工具（Python 3.7+ 可执行脚本）
├── rlpx-verify-helper             # Go RLPx 握手验证工具（可选，预编译二进制）
├── rlpx-verify/                   # Go helper 源码目录
│   ├── main.go                    # Go 源码
│   ├── go.mod                     # Go 模块定义
│   ├── Makefile                   # 构建脚本
│   └── README.md                  # Go helper 文档
└── requirements-advanced.txt      # Python 高级 P2P 测试依赖（可选）
```

### 依赖

**基础依赖（必需）:**

- Python 3.7+
- kubectl (已配置并有集群访问权限)
- 集群中的 pods 需要支持 `curl` 或 `wget` (用于 RPC 调用，工具会自动检测并使用可用的命令)

**完整 RLPx 握手支持（推荐）:**

使用预编译的 Go helper 工具获得完整的 RLPx 握手验证：

1. **使用预编译二进制**（推荐）：

   ```bash
   # 二进制已包含在 tools/ 目录中，无需额外操作
   # 工具会自动检测并使用 rlpx-verify-helper
   ```

2. **从源码构建**（可选）：

   ```bash
   cd rlpx-verify
   make build      # 构建
   make install    # 安装到 tools/ 目录
   ```

   要求：

   - Go 1.21+
   - 自动下载 `go-ethereum` 依赖

**Python 高级 P2P 测试依赖（降级选项）:**

如果没有 Go helper，可以安装 Python 库作为降级方案（注意：简化实现，握手通常会超时）：

```bash
cd /path/to/tools
pip install -r requirements-advanced.txt
```

包含:

- `eth-keys` - Ethereum 密钥操作和简化 RLPx 握手
- `rlp` - RLP 编码/解码
- `cryptography` - 加密原语

**功能对比:**

| 功能 | 基础模式 | + Python 库 | + Go Helper |
|------|---------|-----------|------------|
| TCP 连接测试 | ✓ | ✓ | ✓ |
| RPC peer ID 获取 | ✓ | ✓ | ✓ |
| devp2p RLPx 握手 | ✗ | ⚠️ (简化,会超时) | ✓✓✓ (完整) |
| Peer ID 验证 | ✗ | ✗ | ✓ |
| 加密握手 (ECIES) | ✗ | ✗ | ✓ |
| libp2p multistream | 基础 | 基础 | 基础 |

**推荐配置:**

- 生产环境调试：使用 Go helper（完整验证）
- 快速检查：仅基础模式（TCP + RPC，足够 99% 场景）
- 开发测试：Python 库（可选，用于测试降级路径）

### 贡献

欢迎贡献新功能!建议的开发方向:
1. 实现上述"建议的额外功能"
2. 添加更多 P2P 协议支持
3. 改进错误处理和用户体验
4. 添加单元测试

---

## References

- [LB4 Multiplexer 项目文档](../README.md)
- [AWS NLB 设置指南](../aws-nlb-setup.md)
- [Geth Admin API](https://geth.ethereum.org/docs/interacting-with-geth/rpc/ns-admin#admin-nodeinfo)
- [OP Node P2P API](https://docs.optimism.io/node-operators/reference/json-rpc#opp2p-self)
