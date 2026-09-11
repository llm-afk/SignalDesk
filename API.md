# 信号池接口约定

## 当前值与历史分开

```python
from signaldesk import SignalStore, SignalMeta, ControlModel, ControlSpec

pool = SignalStore()
controls = ControlModel(pool)
controls.add(ControlSpec('local.target', '目标值', minimum=-10, maximum=10,
                         step=0.01, history_hz=50))
controls.set_value('local.target', 2.5)  # 立即更新当前值
print(pool.latest('local.target'))      # (timestamp, value)
controls.tick()                        # 宿主定时调用，按各控件节奏写历史
```

宿主必须使用统一的秒单位单调时钟。默认均为 `time.perf_counter`；示例应用使用减去启动时刻后的同一时钟，不能混用绝对单调时间和从零开始的时间。每个信号历史时间戳严格递增。`set_value` 仅更新当前值，`append_samples` 批量写历史并更新当前值。

## 独立订阅

控件身份与信号身份分开。`ControlSpec.id` 是唯一控件 ID，`signal_id` 是绑定信号 ID，省略时使用控件 ID，兼容旧配置。`controls.set_value/press/release/value` 的参数是控件 ID；`pool.latest/subscribe` 使用信号 ID。

```python
controls.add(ControlSpec('enable_button', '使能', kind='button', button_mode='set',
                         minimum=0, maximum=1, high=1, signal_id='motor.enabled'))
controls.add(ControlSpec('disable_button', '失能', kind='button', button_mode='set',
                         minimum=0, maximum=1, high=0, signal_id='motor.enabled'))
controls.press('enable_button')
controls.release('enable_button')
assert pool.latest('motor.enabled')[1] == 1
controls.press('disable_button')
controls.release('disable_button')
assert pool.latest('motor.enabled')[1] == 0
```

已有信号绑定时不重新注册、不覆盖初值或显示名称。多个控件对同一变量按操作顺序写入，最后写入生效；波形历史统一按绑定控件中的最高 Hz 记录，任一周期策略优先于变化策略。事件归属于目标信号。重新配置控件不钳位或重置共享当前值，范围约束仅作用于该控件的后续写入。

```python
changed = pool.subscribe(on_targets, keys=['local.target'], mode='change', hz=50)
periodic = pool.subscribe(on_snapshot, keys=['local.target'], mode='periodic', hz=200)
edges = pool.subscribe(on_edges, keys=['local.switch'], events=True)

pool.dispatch()   # 宿主定时调用，与 controls.tick 分开
changed.close()  # 显式退订，释放回调引用
```

值回调收到 `{signal_id: (timestamp, value)}` 的独立快照。`change` 首次分发已有快照，之后只在值变化时通知；带 Hz 时在限速窗口合并为最新值。没有 Hz 时，每次 dispatch 最多通知一次。`periodic` 每周期提供最新快照，Hz 应显式填写；没有新值时保留最后值及其时间戳，消费者可以自行判断是否陈旧。

事件回调收到不可变 `EventBatch(events, cursor, lost)`。每条事件含 `sequence / signal_id / kind / value / timestamp`。新事件订阅从订阅时刻开始，不重放旧按键。事件不允许配置限速或周期模式。主动拉取可以使用 `pool.read_events(after=cursor)`，各消费者使用独立游标；`lost` 表示落后于有界事件缓存所丢失的全局事件数量。

`pool.dispatch()`、元数据管理、控件模型、批次入库和订阅注册/关闭都在宿主所有者线程执行。回调应很短，不在回调中进行阻塞的设备 I/O。通信线程可以安全地读取 `pool.snapshot()` / `pool.latest()` / `pool.read_events()`，按自己的真实时钟调度，然后在自己的线程发送协议；不能从工作线程读取 Qt 控件或直接操作绘图历史数组。

订阅者异常保存在最多 32 条 `pool.subscriber_errors` 中，其他消费者继续运行。应用应在需要时展示/记录该诊断；GUI 快照值不能作为设备执行确认。

## 外部数据入口

```python
from signaldesk import SignalMeta
from signaldesk.inbox import BatchInbox, PoolIngress

pool.register_signal(SignalMeta('sensor.position', '实际位置', unit='rad'))
inbox = BatchInbox()
ingress = PoolIngress(pool, epoch=0.0)

# 生产线程：这里的时间戳必须已映射到共同的时钟。
inbox.publish('sensor.position', [1.000, 1.001], [0.1, 0.2])

# GUI / 所有者线程：
ingress.consume(inbox)
pool.dispatch()
```

批次入口复制生产者数组以获得独立所有权；最多 65536 点 / 512 批，过载丢最旧批并累计 `dropped`。入库拒绝未知 ID、重复和迟到时间点，累计 `rejected_points`。不同采样率不互相重采样，来源之间的时间偏移与漂移需要接入适配器处理。

当前入口没有规定任何帧头、对象 ID、网络地址或下位机命令。以后对接协议时，只需实现“解码成批次”以及“按业务需要订阅值/事件”两侧适配，不修改三个核心组件。
