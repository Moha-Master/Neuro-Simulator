// src/types/common.ts

// 输出包结构定义
export interface OutputPack {
    type: string;        // 输出类型，如 "speak", "model_action", "chat_message"
    timestamp: number;   // 时间戳
    payload: any;        // 具体载荷内容
    metadata: {
        agent_type: string;    // "neuro" 或 "chatbot"
        source_tool: string;   // 原始工具名
    };
}

// 定义后端 WebSocket 消息的通用结构
export interface WebSocketMessage {
    type: string;
    [key: string]: any; // 允许其他任意属性
}

// 直播状态同步消息
export interface StreamStateSyncMessage extends WebSocketMessage {
    type: "stream_state_sync";
    state: {
        current_phase: string;
        welcome_video_progress: number; // 秒
        neuro_avatar_stage: string;
        is_neuro_speaking: boolean;
        elapsed_time_since_stream_start: number;
    };
}

// Neuro 语音片段消息
export interface NeuroSpeechSegmentMessage extends WebSocketMessage {
    type: "neuro_speech_segment";
    segment_id?: number; // 片段 ID，可选
    text?: string;       // 字幕文本，可选
    audio_base64?: string; // 音频数据 Base64，可选
    duration?: number;   // 音频时长，可选
    is_end: boolean;     // 是否是本次发言的最后一个片段
}

// 直播元数据更新消息
export interface StreamMetadataMessage extends WebSocketMessage {
    type: "update_stream_metadata";
    streamer_nickname: string;
    stream_title: string;
    stream_category: string;
    stream_tags: string[];
}

// 聊天消息
export interface ChatMessage extends WebSocketMessage {
    type: "chat_message";
    username: string;
    text: string;
    is_user_message: boolean; // 是否是当前客户端用户发送的消息
}

// 后端错误消息
export interface BackendErrorMessage extends WebSocketMessage {
    type: "error";
    code: string;
    message: string;
    text_segment?: string; // 如果是 TTS 相关的错误，可能包含出错的文本片段
}

// 前端发送的用户消息
export interface UserInputMessage {
    type: "user_message";
    message: string;
    username: string;
}

// 前端发送的 TTS 完成信号
export interface TTSFinishedMessage {
    type: "tts_finished";
}

// 前端发送的立绘阶段更新 (可选，如果前端自主管理立绘动画)
export interface NeuroAvatarStageUpdateMessage {
    type: "neuro_avatar_stage_update";
    stage: string; // "hidden", "step1", "step2"
}

// 静音按钮元素类型
export type MuteButtonElement = HTMLButtonElement;

// 定义直播阶段常量，与后端保持一致
export const StreamPhase = {
    OFFLINE: "offline",
    INITIALIZING: "initializing",
    AVATAR_INTRO: "avatar_intro",
    LIVE: "live",
} as const; // 使用 as const 确保它是只读的字面量类型

// 定义 Neuro 立绘阶段常量，与后端保持一致
export const NeuroAvatarStage = {
    HIDDEN: "hidden",
    STEP1: "step1",
    STEP2: "step2",
} as const;
