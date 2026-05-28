# GUI → API 映射表

基于 `video_processor_gui.py` 所有按钮/事件分析，建立 Tkinter GUI 到 Web API 的完整映射。

## 事件映射总表

| event_id | desktop_handler | api_path | method | sse | 说明 |
|----------|-----------------|----------|--------|-----|------|
| start_processing | start_processing | POST /api/process/start | POST | ✅ | 启动视频处理任务 |
| clear_inputs | clear_inputs | POST /api/process/clear | POST | ❌ | 清空当前任务状态 |
| open_output_folder | open_output_folder | GET /api/output/path | GET | ❌ | 获取输出文件夹路径 |
| get_platforms | (combo values) | GET /api/platforms | GET | ❌ | 获取平台列表及配置 |
| get_logs | log_message | GET /api/process/logs/{task_id} | GET | ✅ | SSE 实时日志流 |
| get_task_status | (progress var) | GET /api/process/status/{task_id} | GET | ✅ | 任务状态与进度 |
| download_video | download_video | POST /api/process/download | POST | ❌ | 下载视频（内部） |
| speech_to_text | speech_to_text | POST /api/process/transcribe | POST | ❌ | 语音转文字（内部） |
| generate_document | generate_document | POST /api/process/generate-doc | POST | ❌ | 生成 Markdown（内部） |

## SSE 事件协议

```
event: log
data: {"timestamp": "14:30:05", "level": "INFO", "message": "步骤1: 下载视频..."}

event: progress
data: {"stage": "downloading", "progress": 35, "status": "处理中..."}

event: complete
data: {"ok": true, "doc_filename": "小红书_视频分析_abc123_1715472000.md"}

event: error
data: {"ok": false, "error": "下载失败", "stage": "download"}
```

## API 详细 Schema

### POST /api/process/start
```json
// Request
{"platform": "小红书", "link": "https://..."}
// Response (202 Accepted)
{"task_id": "uuid", "status": "processing"}
```

### GET /api/output/path
```json
// Response
{"path": "F:/java/AIOPS/.../output", "files": ["file1.md", "file2.md"]}
```

### GET /api/platforms
```json
// Response
{"platforms": ["小红书", "抖音", "B站"]}
```
