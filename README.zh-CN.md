# 无线电干扰源环境模拟器（本地复刻版）

[English](README.md) | 简体中文

试题：[2026 年高教社杯全国大学生数学建模竞赛 B 题](https://www.mcm.edu.cn/html_cn/node/27b6e148f8113f09b0269f64a02629fb.html)

2026 年高教社杯全国大学生数学建模竞赛 **B 题「无线电干扰源的快速自动定位与清除」**
官方模拟器的本地复刻实现。

机器人接口——端口、四条指令、请求/响应字段、错误码、计时规则与幂等规则——逐条对照
官方《模拟器使用说明》（附件 1）与《模拟器通信接口说明及编程指南》（附件 2）实现；
在此之上额外提供网页管理界面、模拟参数自定义与机器狗可视化。无法本地复刻的部分见
[与官方模拟器的差异](#与官方模拟器的差异)。

## 快速开始

```bash
cd simulator
python simulator.py            # 默认端口 2026，自动打开浏览器
```

- 管理界面：<http://127.0.0.1:2026/>
- 机器人接口：`POST http://127.0.0.1:2026/enter | /measure | /clear | /exit`
- 仅需 Python 3.9+，零第三方依赖；仅监听本机回环地址（与官方一致）
- 常用参数：`--port 3000`（换端口）、`--no-open`（不自动打开浏览器）

典型用法：界面点「问题 3 演练测试」→ 5 秒倒计时 → 界面显示“机器狗接口已就绪” →
运行机器狗程序。

## 模拟器操作指南

**问题 3 演练**、**问题 4 演练**、**问题 3 正式**、**问题 4 正式** 四个测试模块与官方一致：

- **演练测试不限次数。** 问题 3 和问题 4 的正式测试各有 3 次机会；正式测试开始前会
  再次确认，启动即占用一次机会，中止同样占用。
- 确认开始后，模拟器先完成数据准备，再显示 5 秒倒计时。倒计时结束时开启 25 分钟
  测试窗口并开放机器狗接口。
- 机器狗成功调用 `/enter` 后开始计算最长 20 分钟的程序运行时间；现实截止时刻为窗口
  截止与程序截止的较早者。虚拟世界活动时长上限为 100 小时。
- 机器狗调用 `/exit`、手工中止、窗口超时、程序运行超时或虚拟时间超时都会结束测试，
  界面显示结束原因。测试结束后不能复位或继续原测试，只能开始新的一次测试。
- 「中止测试」需两次确认。机器狗程序卡死或从未调用 `/enter` 时可手工中止，无需等待
  窗口超时。
- 「指令与反馈」区域显示机器狗请求及模拟器反馈，默认仅显示最新 1000 条（可在设置中
  调整为 100–5000）。这只是界面显示数量，不限制请求总数，完整行为仍会写入日志。
- 每局测试显示一个**测试案例编码**。演练测试结束后可显示本局干扰源总数、全向数量与
  定向数量；正式测试不显示案例真值。
- 每局测试结束自动生成行为日志（本复刻版为明文 JSON）。在「测试日志」页可导出 JSON
  文件、删除单条日志、一键清空全部日志，或一键在系统文件管理器中打开日志文件夹。

## 批量跑测

「批量跑测」页可以全自动连跑多局并汇总统计，用于评测机器人程序的稳定性：

- **分组自定义**：一次批量可包含多个分组，每组指定问题（3/4）、模式（演练/正式）、
  局数与机器人程序命令（按 shell 执行，如 `python my_robot.py` 或 exe 完整路径），
  例如问题 3 与问题 4 各跑 10 局。正式模式同样消耗正式测试次数。
- **逐局流程**：自动开局 → 倒计时结束接口开放后自动启动机器人程序 → 等待本局结束
  （每局超时可配，默认为倒计时+窗口+60 秒，超时自动中止并回收进程）→ 记录该局
  摘要，局间可设间隔。机器人程序的标准输出/错误保存在
  `simulator/data/batches/robot_logs/<案例编码>.log`，界面可直接查看。
- **接入信息**：机器人程序从环境变量读取：`SIMULATOR_PORT`、`SIMULATOR_BASE_URL`、
  `SIMULATOR_CASE_CODE`、`SIMULATOR_PROBLEM`、`SIMULATOR_MODE`、`SIMULATOR_SEED`。
- **种子与复现**：可为批量指定种子列表（循环使用并逐局记录）。相同种子生成完全
  相同的案例；用同一组种子分别跑两个机器人程序，即可在同难度案例集上公平对比。
  批量忽略设置页的随机种子。
- **统计结果**：结束后自动汇总——完成局数、整体清除率、全清率、结束原因分布、
  超时/失败局数，以及逐局分布的均值/标准差/最小/中位/最大（已清除数量、清除比例、
  检测次数、清除成功/失败、虚拟时间、平均定位清除耗时、程序运行时间、移动距离），
  总体与分组分别给出。报告保存在 `simulator/data/batches/`，可导出 JSON / CSV，
  历史批次可随时查看或删除（不影响单局测试日志）。
- 批量进行中不允许开始单局测试或修改设置；可随时「停止批量」（中止当前局并跳过
  剩余局，已完成局数的统计保留）。
- 注意：模拟按真实时间推进，批量耗时 = 各局真实用时之和（每局最少约等于机器人
  实际运行时间）。调试时可先在设置页缩短测试窗口/程序限时。

## 机器狗行为规则

初始状态：机器狗初始位置 (0, 0)，测向机初始频道为 1。移动与切换频道没有独立指令，
由模拟器根据 `/measure`、`/clear` 的位置和频道参数自动推断。

| 动作 | 指令 | 虚拟世界耗时（秒） |
|---|---|---|
| 开始 | `POST /enter` | 0 |
| 结束 | `POST /exit` | 0 |
| 检测 | `POST /measure` | 5 |
| 精确定位并清除 | `POST /clear` | 3（未发现）/ 5（已清除） |
| 测向机切换频道 | 由 `/measure` 频道参数体现 | 1 |
| 移动 | 由 `position` 参数体现 | 直线距离 ÷ 5 m/s |

## 指令与虚拟世界计时规则

- 只有请求被接受（`accepted=true`）才可能推进虚拟时钟；`/enter`、`/exit` 不推进。
  `accepted=false` 时动作不生效，响应里的 `virtual_time_s` 为 0——它**不是**当前虚拟
  时刻，记录虚拟时刻应使用最近一次 `accepted=true` 的响应。
- 一次合法 `/measure` 耗时 = 移动耗时 + 切换频道耗时（与当前频道不同时为 1 秒）+ 5 秒；
  合法检测完成后测向机当前频道更新为本次检测频道。
- 一次合法 `/clear` 耗时 = 移动耗时 + 3 秒（未发现）/ 5 秒（清除成功）；`/clear` 不切
  换测向机频道。成功时的 5 秒 = 光学精确定位 3 秒 + 激光清除 2 秒；未发现时仅执行
  光学精确定位（3 秒），不启动激光枪。
- `virtual_time_s` 是 JSON 数值，最多保留 6 位小数。

官方规范（附件 2 第 10 节）的完整计时示例：

| 步骤 | 指令 | 位置 | 频道含义 | 移动(米) | 移动(秒) | 切换(秒) | 动作(秒) | 总耗时(秒) | 虚拟时刻(秒) |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `/enter` | – | – | 0 | 0 | 0 | 0 | 0 | 0 |
| 2 | `/measure` | (300,400) | 检测频道 1 | 500 | 100 | 0 | 5 | 105 | 105 |
| 3 | `/measure` | (300,400) | 检测频道 2 | 0 | 0 | 1 | 5 | 6 | 111 |
| 4 | `/clear` | (300,0) | 目标频道 3 | 400 | 80 | 0 | 3 | 83 | 194 |
| 5 | `/measure` | (300,0) | 检测频道 2 | 0 | 0 | 0 | 5 | 5 | 199 |
| 6 | `/exit` | – | – | 0 | 0 | 0 | 0 | 0 | 199 |

第 4 步的频道是*目标*干扰源频道；`/clear` 不切换测向机，因此第 5 步仍保持频道 2，
没有切换频道耗时。

## 通信格式总规则

四条指令均使用 HTTP + JSON：

- 方法为 `POST`；路径必须精确为 `/enter`、`/measure`、`/clear` 或 `/exit`（不接受
  尾随斜线和查询参数）。
- `Content-Type` 必须为 `application/json`（仅允许参数 `charset=utf-8`）；
  `Content-Encoding` 可省略或为 `identity`。否则返回 415。
- 请求体必须是无 BOM 的 UTF-8 JSON 对象，不能有重复键，嵌套不超过 16 层，最大
  65536 字节。否则返回 400/413。
- `channel` 必须是 1..20 的整数（`1.0` 可接受；`1.5` → 400）；坐标分量必须是有限数
  值且绝对值不超过 2000000。
- 未声明的字段**不会被忽略**：模拟器返回 HTTP 200 + `accepted=false`，帮助发现拼写
  错误。
- `arena_id` 必须是 ASCII 字符串 `"default"`。
- `robot_id` 必须是 UTF-8 长度 1–64 字节的非空字符串（见
  [与官方模拟器的差异](#与官方模拟器的差异)）；`request_id` 是幂等键，1–128 字节。
  二者都不能包含控制字符或不可见格式字符。

所有 JSON 业务响应都至少包含：

| 字段 | 类型 | 含义 |
|---|---|---|
| `accepted` | boolean | 模拟器是否接受本次请求 |
| `real_timestamp_ms` | number | 模拟器响应时的现实世界时间戳，单位毫秒 |
| `virtual_time_s` | number | 虚拟时钟，单位秒 |

`accepted=false` 时响应只包含以上 3 个字段。

| HTTP 状态 | 含义 |
|---|---|
| 200，`accepted=true` | 动作已执行 |
| 200，`accepted=false` | JSON 结构合法但被当前测试状态拒绝；存在未知字段；arena_id/robot_id 不匹配 |
| 400 | JSON 语法、重复键、缺失字段、字段类型、标识符格式、频道或坐标范围错误 |
| 404 | 路径未知或路径不精确 |
| 405 | 已知路径使用了非 POST 方法 |
| 409 | 同一 request_id 对应了不同内容，或并发发送了不同动作 |
| 413 | 请求体超过 65536 字节 |
| 415 | Content-Type 或 Content-Encoding 不受支持 |
| 429 | 无效流量保护触发，或本局幂等记录达到上限 |
| 500 | 模拟器内部错误 |

幂等规则：**每个新动作使用新的 `request_id`**；仅在网络故障后重试完全相同的动作时
复用原请求内容和原 `request_id`。同 ID 同内容返回第一次的完整响应；同 ID 不同内容
返回 409。结构错误、未知字段与标识符不匹配不占用该 ID。

并非所有错误都能形成 HTTP 响应：倒计时未结束、接口未开放或测试已结束时，连接可能
被直接关闭、没有 JSON 体。机器狗程序必须同时处理“连不上”和“收到 JSON”——务必同时
检查 HTTP 状态**和** `accepted`。

## 指令说明（官方示例）

### POST /enter —— 进入目标区域

```json
{
  "arena_id": "default",
  "robot_id": "<参赛队号>",
  "request_id": "enter-1"
}
```

接受时的响应（不推进虚拟时钟）：

```json
{
  "accepted": true,
  "real_timestamp_ms": 1760000000000,
  "virtual_time_s": 0,
  "max_virtual_duration_s": 360000,
  "max_real_duration_s": 1200,
  "remaining_real_duration_s": 1200
}
```

`remaining_real_duration_s` 是本局实际还能使用的现实时间秒数（0..1200 整数），不要
固定假定每次都有 1200 秒。

### POST /measure —— 检测

```json
{
  "arena_id": "default",
  "robot_id": "<参赛队号>",
  "request_id": "measure-1",
  "position": {"x": 300, "y": 400},
  "channel": 1
}
```

三种检测结果：

```json
{"accepted": true, "real_timestamp_ms": 1760000000000,
 "virtual_time_s": 105, "measure_result": "no_signal"}
```

```json
{"accepted": true, "real_timestamp_ms": 1760000000000,
 "virtual_time_s": 105, "measure_result": "near"}
```

```json
{"accepted": true, "real_timestamp_ms": 1760000000000,
 "virtual_time_s": 105, "measure_result": "direction", "svd_deg": 123.45}
```

- `no_signal` —— 当前位置接收不到该频道信号（该频道无未清除干扰源 / 超出 1000–1500
  米的有效接收半径 / 不在定向干扰源 ±90° 覆盖范围内）。
- `near` —— 已检测到信号但与干扰源距离不超过 5 米，不返回 `svd_deg`。
- `direction` —— `svd_deg` 为干扰源示向度（0° 为正东、逆时针为正、[0,360)），误差
  ±1°，保留两位小数。不应把单次示向度当作精确真实方位。

### POST /clear —— 精确定位并清除

```json
{
  "arena_id": "default",
  "robot_id": "<参赛队号>",
  "request_id": "clear-1",
  "position": {"x": 300, "y": 0},
  "channel": 3
}
```

两种清除结果：

```json
{"accepted": true, "real_timestamp_ms": 1760000000000,
 "virtual_time_s": 194, "clear_result": "no_target_in_range"}
```

```json
{"accepted": true, "real_timestamp_ms": 1760000000000,
 "virtual_time_s": 196, "clear_result": "success"}
```

指令发出后先启用光学探测仪精确定位，耗时 3 秒；若在 20 米范围内发现目标，则立即启用
激光枪清除，再耗时 2 秒。清除半径为 20 米，与定向干扰源朝向无关。同一干扰源只能清除
一次，重复清除返回 `no_target_in_range`。频道参数仅指定要清除的目标频道，不会切换
测向机。

### POST /exit —— 退出目标区域

```json
{
  "arena_id": "default",
  "robot_id": "<参赛队号>",
  "request_id": "exit-1"
}
```

```json
{
  "accepted": true,
  "real_timestamp_ms": 1760000000000,
  "virtual_time_s": 199,
  "exit_reason": "user_exit"
}
```

## 官方示例程序

以下代码（附件 2 第 11 节）只演示如何发送四类请求并读取响应，不包含查找策略。

Python 版：

```python
import json
from urllib.request import Request, urlopen

# 修改为你们自己的参赛队号。本复刻版中任意非空字符串均可作为 robot_id。
BASE_URL = "http://127.0.0.1:2026"
ROBOT_ID = "<参赛队号>"


def post(path, payload):
    request = Request(
        BASE_URL + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as http_response:
        response = json.loads(http_response.read().decode("utf-8"))
    print(path, response)
    return response


def base(request_id):
    return {
        "arena_id": "default",
        "robot_id": ROBOT_ID,
        "request_id": request_id,
    }


def action(request_id, x, y, channel):
    payload = base(request_id)
    payload["position"] = {"x": x, "y": y}
    payload["channel"] = channel
    return payload


def main():
    enter_response = post("/enter", base("enter-1"))
    if enter_response.get("accepted") is not True:
        print("进入失败")
        return

    remaining_time = enter_response["remaining_real_duration_s"]
    print("本局可用现实时间：", remaining_time, "秒")

    # 与第 10 节计时示例完全相同的四个动作。
    actions = [
        ("/measure", action("measure-1", 300, 400, 1)),
        ("/measure", action("measure-2", 300, 400, 2)),
        ("/clear", action("clear-1", 300, 0, 3)),
        ("/measure", action("measure-3", 300, 0, 2)),
    ]

    for path, payload in actions:
        # 如果因网络故障重试本动作，必须复用这个 payload 及其中的 request_id。
        response = post(path, payload)
        if response.get("accepted") is not True:
            print("请求未执行")
            return

        if path == "/measure":
            if response["measure_result"] == "direction":
                print("示向度：", response["svd_deg"], "度")
            elif response["measure_result"] == "near":
                print("距离过近，没有示向度")
            else:
                print("未测得信号")
        else:
            if response["clear_result"] == "success":
                print("清除成功")
            else:
                print("清除位置附近没有目标")

    exit_response = post("/exit", base("exit-1"))
    if exit_response.get("accepted") is True:
        print("退出原因：", exit_response["exit_reason"])


main()
```

MATLAB 版：

```matlab
function demo_robot()

% 修改为你们自己的参赛队号。
baseUrl = 'http://127.0.0.1:2026';
robotId = '<参赛队号>';
options = weboptions('MediaType', 'application/json', 'Timeout', 5);

enterRequest = baseRequest(robotId, 'enter-1');
enterResponse = post(baseUrl, '/enter', enterRequest, options);
if enterResponse.accepted ~= true
    fprintf('进入失败\n');
    return;
end

remainingTime = enterResponse.remaining_real_duration_s;
fprintf('本局可用现实时间：%d秒\n', remainingTime);

% 与第 10 节计时示例完全相同的四个动作。
actions = {
    '/measure', actionRequest(robotId, 'measure-1', 300, 400, 1);
    '/measure', actionRequest(robotId, 'measure-2', 300, 400, 2);
    '/clear',   actionRequest(robotId, 'clear-1', 300, 0, 3);
    '/measure', actionRequest(robotId, 'measure-3', 300, 0, 2)
};

for i = 1:size(actions, 1)
    path = actions{i, 1};
    payload = actions{i, 2};
    % 如果因网络故障重试本动作，必须复用这个 payload 及其中的 request_id。
    response = post(baseUrl, path, payload, options);
    if response.accepted ~= true
        fprintf('请求未执行\n');
        return;
    end

    if strcmp(path, '/measure')
        if strcmp(response.measure_result, 'direction')
            fprintf('示向度：%.2f度\n', response.svd_deg);
        elseif strcmp(response.measure_result, 'near')
            fprintf('距离过近，没有示向度\n');
        else
            fprintf('未测得信号\n');
        end
    else
        if strcmp(response.clear_result, 'success')
            fprintf('清除成功\n');
        else
            fprintf('清除位置附近没有目标\n');
        end
    end
end

exitRequest = baseRequest(robotId, 'exit-1');
exitResponse = post(baseUrl, '/exit', exitRequest, options);
if exitResponse.accepted == true
    fprintf('退出原因：%s\n', exitResponse.exit_reason);
end
end

function request = baseRequest(robotId, requestId)
request = struct( ...
    'arena_id', 'default', ...
    'robot_id', robotId, ...
    'request_id', requestId);
end

function request = actionRequest(robotId, requestId, x, y, channel)
request = baseRequest(robotId, requestId);
request.position = struct('x', x, 'y', y);
request.channel = channel;
end

function response = post(baseUrl, path, payload, options)
response = webwrite([baseUrl path], payload, options);
fprintf('%s  %s\n', path, jsonencode(response));
end
```

## 编程注意事项

- 先在模拟器界面启动测试，等待界面显示机器狗接口已经就绪。
- 每个新动作使用新的 `request_id`；仅在重试完全相同的动作时复用原 ID 和原请求内容。
- 逐次等待响应，不得并发发送不同动作；正常串行合法动作没有速率上限。
- 同时检查 HTTP 状态**和** `accepted`。`accepted=false` 时动作没有生效，响应中的
  `virtual_time_s=0` 不是当前虚拟时刻。
- 用 `/enter` 返回的 `remaining_real_duration_s` 控制现实运行时间，不要固定假定
  每次都有 1200 秒。
- 每次检测增加的 5 秒是虚拟耗时，现实中不需要等待 5 秒。
- 只有 `measure_result="direction"` 时才读取 `svd_deg`，且它带有误差。
- `/clear` 不切换测向机频道；只有合法的 `/measure` 会更新当前频道。
- `no_signal` 不等于附近一定没有干扰源——可能是超出接收距离或不在定向覆盖范围内。
- 测试结束后接口关闭，不要调用 `/exit` 查询原因，结束原因由界面显示。
- 程序判断结果时使用 `direction`、`near`、`no_signal`、`success`、
  `no_target_in_range`、`user_exit` 等实际字符串，不依赖界面中文说明。
- 机器狗程序应自行记录测试过程中的指令序列、响应信息等内容（本复刻版会额外生成
  行为日志，官方模拟器没有这一功能）。

## 与官方模拟器的差异

官方的在线登录、服务器时间校验与加密日志上传无法在本地复刻，因此：

- **无账号登录、无 17:30 截止时间。** 任意非空 `robot_id`（1–64 字节）均可接入，
  协议字段本身不变。
- 日志为明文 JSON，保存在 `simulator/data/logs/`，可在「测试日志」页导出、删除或
  打开所在文件夹。
- 管理界面由同端口 `/` 与 `/api/*` 路径提供；机器人接口（上述四条精确路径）不受
  影响。
- 模拟参数可在设置页自定义（默认值 = 官方值）：端口、指令与反馈显示条数、目标区域
  半径、干扰源个数/频道/接收半径范围、示向度误差、随机种子（指定后案例可复现）、
  移动速度、各动作耗时、近距离阈值、清除半径、初始频道、倒计时/窗口/程序/虚拟时限、
  正式测试次数，以及自定义干扰源案例编辑器。正式测试次数可重置。
- 额外的本地辅助功能：可视化页（实时地图 + 回放机器狗、干扰源、示向度射线与轨迹）、
  批量跑测页（自动连跑多局并汇总统计，见上文）与正式测试“显示真值”调试开关
  （演练始终可见）。

## 数据位置

配置、正式测试次数与日志保存在 `simulator/data/`；批量跑测报告与机器人输出日志
保存在 `simulator/data/batches/`。移动模拟器时请整体移动该目录。

## 许可证

[MIT](LICENSE)
