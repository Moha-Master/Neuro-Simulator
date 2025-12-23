// src/core/appInitializer.ts

import { WebSocketClient } from '../services/websocketClient';
import { AudioPlayer } from '../services/audioPlayer';
import { VideoPlayer } from '../stream/videoPlayer';
import { NeuroAvatar } from '../stream/neuroAvatar';
import { ChatDisplay } from '../ui/chatDisplay';
import { showNeuroCaption, hideNeuroCaption } from '../ui/neuroCaption';
import { UserInput, MessagePayload } from '../ui/userInput';
import { LayoutManager } from './layoutManager';
import { StreamTimer } from '../ui/streamTimer';
import { ChatSidebar } from '../ui/chatSidebar';
import { LiveIndicator } from '../ui/liveIndicator';
import { StreamInfoDisplay } from '../ui/streamInfoDisplay';
import { WakeLockManager } from '../utils/wakeLockManager';
import { WebSocketMessage, ChatMessage, NeuroSpeechSegmentMessage, StreamMetadataMessage } from '../types/common';
import { SettingsModal, AppSettings } from '../ui/settingsModal';
import { MuteButton } from '../ui/muteButton';
import { getLatestReplayVideo, buildBilibiliIframeUrl } from '../services/bilibiliService';
import { IS_TAURI } from '../utils/env';
import { getSettings, saveSettings } from '../services/settingsService';

export class AppInitializer {
    private wsClient: WebSocketClient | null;
    private audioPlayer: AudioPlayer;
    private videoPlayer: VideoPlayer;
    private neuroAvatar: NeuroAvatar;
    private chatDisplay: ChatDisplay;
    private userInput: UserInput;   
    private layoutManager: LayoutManager;
    private streamTimer: StreamTimer;
    private chatSidebar: ChatSidebar;
    private liveIndicator: LiveIndicator;
    private streamInfoDisplay: StreamInfoDisplay;
    private wakeLockManager: WakeLockManager;
    
    private settingsModal: SettingsModal;
    private currentSettings: AppSettings;
    private muteButton: MuteButton;
    private resizeObserver: ResizeObserver | null = null;
    private offlinePlayerSrc: string | null = null;
    private logoContainer: HTMLElement | null = null;

    private isStarted: boolean = false;
    private currentPhase: string = 'offline';
    private isConnectionLost: boolean = false;

    constructor() {
        this.layoutManager = new LayoutManager();
        this.streamTimer = new StreamTimer();
        this.muteButton = new MuteButton();
        
        this.settingsModal = new SettingsModal((newSettings) => this.handleSettingsUpdate(newSettings));

        // 初始化 currentSettings 为默认值，稍后在 init() 中异步加载
        this.currentSettings = this.settingsModal.getDefaultSettings();

        // 初始化 wsClient 为 null，等待 start 方法中的探测逻辑
        this.wsClient = null;

        this.audioPlayer = new AudioPlayer();
        this.videoPlayer = new VideoPlayer();
        this.neuroAvatar = new NeuroAvatar();
        this.chatDisplay = new ChatDisplay();
        this.userInput = new UserInput();
        this.userInput.onSendMessage((payload: MessagePayload) => this.sendUserMessage(payload));
        this.chatSidebar = new ChatSidebar();
        this.liveIndicator = new LiveIndicator();
        this.streamInfoDisplay = new StreamInfoDisplay();
        this.wakeLockManager = new WakeLockManager();
        
        this.setupSettingsModalTrigger();
        this.setupMuteButton();

        // Store the original src as a fallback, then try to update it.
        const offlinePlayer = document.querySelector('.offline-video-player') as HTMLIFrameElement;
        if (offlinePlayer) {
            this.offlinePlayerSrc = offlinePlayer.src;
        }
        this.updateOfflinePlayerSrc();
        this.logoContainer = document.querySelector('.twitch-logo-container');
    }

    public async init(): Promise<void> {
        // 异步加载设置
        const loadedSettings = await getSettings();
        if (loadedSettings) {
            this.currentSettings = loadedSettings;
        }
        
        // 之后执行依赖设置的初始化逻辑
        await this.probeForIntegratedServer();
    }

    public start(): void {
        if (this.isStarted) return;
        this.isStarted = true;

        // Then start the layout manager and go offline
        this.layoutManager.start();
        this.updateLogoState('disconnected'); // Set initial logo state
        this.goOffline(); // Start in offline state, connection status will update UI via goOnline
        this.updateUiWithSettings();
        // Connection logic is handled by probeForIntegratedServer which is called in init()
    }

    private setupSettingsModalTrigger(): void {
        const trigger = document.querySelector('.nav-user-avatar-button');
        if (trigger) {
            trigger.addEventListener('click', async () => {
                await this.settingsModal.open();
            });
        }
    }

    private setupMuteButton(): void {
        const muteButtonElement = this.muteButton.create();
        if (muteButtonElement) {
            this.muteButton.show();
            const handleGlobalClick = () => {
                this.muteButton.unmute();
                document.removeEventListener('click', handleGlobalClick);
            };
            document.addEventListener('click', handleGlobalClick);
        }
    }

    public getMuteButton(): MuteButton {
        return this.muteButton;
    }

    public getAudioPlayer(): AudioPlayer {
        return this.audioPlayer;
    }

    public getNeuroAvatar(): NeuroAvatar {
        return this.neuroAvatar;
    }

    private updateLogoState(state: 'disconnected' | 'connected' | 'live'): void {
        if (!this.logoContainer) return;

        this.logoContainer.classList.remove('logo-disconnected', 'logo-connected', 'logo-live');

        switch (state) {
            case 'disconnected':
                this.logoContainer.classList.add('logo-disconnected');
                break;
            case 'connected':
                this.logoContainer.classList.add('logo-connected');
                break;
            case 'live':
                this.logoContainer.classList.add('logo-live');
                break;
        }
    }

    private handleSettingsUpdate(newSettings: AppSettings): void {
        console.log("Settings updated. Re-initializing connection with new settings:", newSettings);
        this.currentSettings = newSettings;
        
        this.updateUiWithSettings();

        if (this.wsClient) {
            const newUrl = newSettings.backendUrl ? `${newSettings.backendUrl}/ws/stream` : '';
            this.wsClient.updateOptions({
                url: newUrl,
                maxReconnectAttempts: newSettings.reconnectAttempts,
            });

            this.wsClient.disconnect();
            
            setTimeout(() => {
                if(this.wsClient && this.wsClient.getUrl()) {
                    this.wsClient.connect();
                } else {
                    console.warn("Cannot connect: Backend URL is empty after update or WebSocket client not ready.");
                }
            }, 500);
        } else {
            console.warn("WebSocket client not initialized, cannot update settings.");
        }
    }

    private initWebSocketClient(backendUrl: string): void {
        const url = backendUrl ? `${backendUrl}/ws/stream` : '';
        const universalMessageHandler = (message: WebSocketMessage) => this.handleWebSocketMessage(message);

        const onOpen = () => {
            this.isConnectionLost = false;
            this.updateLogoState('connected'); // Set logo to connected state
            this.goOnline();
        };

        const onDisconnect = () => {
            // Only trigger goOffline if we are not already in a disconnected state
            if (!this.isConnectionLost) {
                this.isConnectionLost = true;
                this.updateLogoState('disconnected'); // Set logo to disconnected state
                this.goOffline();
            }
        };

        // If wsClient already exists, update its configuration; otherwise, create a new instance
        if (this.wsClient) {
            this.wsClient.updateOptions({
                url: url,
                autoReconnect: true,
                maxReconnectAttempts: this.currentSettings.reconnectAttempts,
                onMessage: universalMessageHandler,
                onOpen: onOpen,
                onDisconnect: onDisconnect,
            });
            // If the new URL is valid, disconnect the old connection and try the new one
            if (url) {
                this.wsClient.disconnect();
                setTimeout(() => {
                    this.wsClient!.connect();
                }, 500);
            }
        } else {
            this.wsClient = new WebSocketClient({
                url: url,
                autoReconnect: true,
                maxReconnectAttempts: this.currentSettings.reconnectAttempts,
                onMessage: universalMessageHandler,
                onOpen: onOpen,
                onDisconnect: onDisconnect,
            });
        }
    }

    private async probeForIntegratedServer(): Promise<void> {
        // First, load the currently stored settings
        const storedSettings = await getSettings();
        const currentSettings = storedSettings || this.settingsModal.getDefaultSettings();
        console.log("Probing for integrated server. Stored settings:", currentSettings);

        // Try to connect to the local Server's health check endpoint
        try {
            // Construct the health check URL using the current page's origin
            const healthUrl = new URL('/api/system/health', window.location.origin).toString();
            console.log("Probing integrated server at:", healthUrl);

            const response = await fetch(healthUrl);
            const contentType = response.headers.get("content-type");

            if (response.ok && contentType && contentType.includes("application/json")) {
                console.log("Integrated server detected via health check. Auto-connecting...");
                // 1. Set to integrated mode, using the current origin
                const integratedBackendUrl = window.location.origin;
                // 2. Update the current settings' backendUrl
                this.currentSettings = { ...currentSettings, backendUrl: integratedBackendUrl };
                // 3. Save to storage for future use using settings service
                await saveSettings(this.currentSettings);
                // 4. Initialize or update the WebSocket client
                this.initWebSocketClient(integratedBackendUrl);
                // 5. Try to connect if the client and URL are valid
                if (this.wsClient && integratedBackendUrl) {
                    this.wsClient.connect();
                }
                return; // Success, exit
            } else {
                console.log("Health check failed, not an integrated server. Status:", response.status);
            }
        } catch (error) {
            console.log("Failed to probe for integrated server, assuming standalone mode.", error);
        }

        // If probing fails, fall back to using the stored settings
        console.log("Falling back to stored backend URL:", currentSettings.backendUrl);
        this.currentSettings = currentSettings;
        this.initWebSocketClient(currentSettings.backendUrl);

        // Attempt to connect if the client and URL are valid
        if (this.wsClient && currentSettings.backendUrl) {
            this.wsClient.connect();
        } else if (!currentSettings.backendUrl) {
            console.warn("Backend URL is not configured via probe or stored settings. Opening settings modal.");
            this.settingsModal.open();
        }
    }

    private updateUiWithSettings(): void {
        const userAvatars = document.querySelectorAll('.user-avatar-img') as NodeListOf<HTMLImageElement>;
        userAvatars.forEach(img => img.src = this.currentSettings.avatarDataUrl);
        console.log(`UI updated with username: ${this.currentSettings.username} and avatar.`);
    }

    private adjustOfflineLayout = () => {
        if (this.currentPhase !== 'offline') return;

        const container = document.getElementById('offline-content-container') as HTMLElement;
        const videoPlayer = document.querySelector('.offline-video-player') as HTMLElement;
        const infoCard = document.querySelector('.offline-info-card') as HTMLElement;

        if (!container || !videoPlayer || !infoCard) return;

        // Check window width to determine layout mode
        const isMobile = window.innerWidth <= 767;

        if (isMobile) {
            // MOBILE MODE: Stack elements, card width matches video width
            container.style.flexWrap = 'wrap';
            infoCard.style.width = videoPlayer.offsetWidth + 'px';
            infoCard.style.height = 'auto';
            infoCard.style.flex = '0 0 auto'; // Do not grow or shrink, use explicit width
        } else {
            // DESKTOP/TABLET MODE: Keep side-by-side, shrink when needed
            container.style.flexWrap = 'nowrap';
            const videoHeight = videoPlayer.offsetHeight;
            if (videoHeight > 0) {
                infoCard.style.height = `${videoHeight}px`;
                infoCard.style.width = `${videoHeight}px`;
            }
            // Flex properties for horizontal shrinking, but DO NOT GROW.
            infoCard.style.flex = '0 1 auto';
        }
    }

    private async updateOfflinePlayerSrc(): Promise<void> {
        console.log('Attempting to fetch the latest replay video...');
        const videoInfo = await getLatestReplayVideo();
        if (videoInfo) {
            const newSrc = buildBilibiliIframeUrl(videoInfo);
            this.offlinePlayerSrc = newSrc;
            console.log('Successfully updated offline player src to:', newSrc);

            // If we are already in the offline phase, update the iframe src immediately
            if (this.currentPhase === 'offline') {
                const offlinePlayer = document.querySelector('.offline-video-player') as HTMLIFrameElement;
                if (offlinePlayer) {
                    offlinePlayer.src = this.offlinePlayerSrc;
                }
            }
        } else {
            console.log('Failed to fetch latest replay video, using default fallback.');
        }
    }

    private goOnline(): void {
        console.log("Entering ONLINE state.");
        this.updateUiWithSettings();

        // --- ADDED: Activate Tauri view mode on any online state ---
        if (IS_TAURI) {
            document.body.classList.add('stream-live-view');
        }

        const offlinePlayer = document.querySelector('.offline-video-player') as HTMLIFrameElement;
        if (offlinePlayer) {
            offlinePlayer.src = 'about:blank';
        }

        // Stop observing and clear styles
        if (this.resizeObserver) {
            this.resizeObserver.disconnect();
            this.resizeObserver = null;
        }
        window.removeEventListener('resize', this.adjustOfflineLayout); // Clean up listener

        const infoCard = document.querySelector('.offline-info-card') as HTMLElement;
        if (infoCard) {
            infoCard.style.height = '';
            infoCard.style.width = '';
        }

        document.getElementById('offline-content-container')?.classList.add('hidden');
        document.getElementById('stream-display-viewport')?.classList.remove('hidden');
        document.querySelector('.stream-info-details-row')?.classList.remove('hidden'); // Show details row
        document.getElementById('chat-sidebar')?.classList.remove('hidden');

        this.showStreamContent();
        this.chatDisplay.clearChat();
        this.liveIndicator.show();
        this.wakeLockManager.requestWakeLock();
    }

    private goOffline(): void {
        console.log("Entering OFFLINE state.");
        this.currentPhase = 'offline';

        // --- ADDED: Deactivate Tauri view mode on offline state ---
        if (IS_TAURI) {
            document.body.classList.remove('stream-live-view');
        }

        const offlinePlayer = document.querySelector('.offline-video-player') as HTMLIFrameElement;
        if (offlinePlayer && this.offlinePlayerSrc) {
            offlinePlayer.src = this.offlinePlayerSrc;
        }

        // Show offline banner and hide online elements
        document.getElementById('offline-content-container')?.classList.remove('hidden');
        document.getElementById('stream-display-viewport')?.classList.add('hidden');
        document.querySelector('.stream-info-details-row')?.classList.add('hidden'); // Hide details row
        document.getElementById('chat-sidebar')?.classList.add('hidden');

        this.hideStreamContent();
        this.audioPlayer.stopAllAudio();
        this.videoPlayer.hide();
        this.neuroAvatar.setStage('hidden', true);
        hideNeuroCaption();
        this.streamTimer.stop();
        this.streamTimer.reset();
        this.chatDisplay.clearChat();
        this.liveIndicator.hide();
        this.wakeLockManager.releaseWakeLock();
        
        this.muteButton.show();
        const handleGlobalClick = () => {
            this.muteButton.unmute();
            document.removeEventListener('click', handleGlobalClick);
        };
        document.addEventListener('click', handleGlobalClick);

        // Adjust layout and set up observers
        setTimeout(() => {
            this.adjustOfflineLayout();
            const videoPlayer = document.querySelector('.offline-video-player');
            if (videoPlayer && !this.resizeObserver) {
                this.resizeObserver = new ResizeObserver(this.adjustOfflineLayout);
                this.resizeObserver.observe(videoPlayer);
                window.addEventListener('resize', this.adjustOfflineLayout); // Also listen to window resize
            }
        }, 0);
    }

    private handleWebSocketMessage(message: WebSocketMessage): void {
        // Check if this is an output pack format message
        if (this.isOutputPack(message)) {
            // Convert output pack to legacy message format
            this.handleOutputPack(message as any);
            return;
        }

        // Handle legacy message format
        if (this.currentPhase === 'offline' && ['play_welcome_video', 'start_avatar_intro', 'enter_live_phase'].includes(message.type)) {
            console.log("Connection successful, transitioning from OFFLINE to active state.");
            this.goOnline(); // Use the new goOnline method
        }

        if (message.elapsed_time_sec !== undefined) {
            this.streamTimer.start(message.elapsed_time_sec);
        }

        switch (message.type) {
            case 'offline':
                this.goOffline();
                break;
            case 'model_spin':
                this.neuroAvatar.triggerSpin();
                break;
            case 'model_zoom':
                this.neuroAvatar.triggerZoom();
                break;
            case 'update_stream_metadata':
                this.streamInfoDisplay.update(message as StreamMetadataMessage);
                break;
            case 'play_welcome_video':
                const progress = parseFloat(message.progress as any);
                // If we are not in the offline phase, the stream is already active, so just seek.
                if (this.currentPhase !== 'offline') {
                    this.videoPlayer.seekTo(progress);
                } else {
                    // This is the first play command.
                    this.currentPhase = 'initializing';
                    this.videoPlayer.showAndPlayVideo(progress);
                }
                break;
            case 'start_avatar_intro':
                this.currentPhase = 'avatar_intro';
                this.videoPlayer.pauseAndMute();
                this.neuroAvatar.startIntroAnimation(() => {
                    this.videoPlayer.hide();
                });
                break;
            case 'enter_live_phase':
                this.updateLogoState('live');
                this.currentPhase = 'live';
                this.videoPlayer.hide();
                this.neuroAvatar.setStage('step2');
                break;
            case 'neuro_is_speaking':
                break;
            case 'neuro_speech_segment':
                const segment = message as NeuroSpeechSegmentMessage;
                if (segment.is_end) {
                    this.audioPlayer.setAllSegmentsReceived();
                } else if (segment.audio_base64 && segment.text && typeof segment.duration === 'number') {
                    this.audioPlayer.addAudioSegment(segment.text, segment.audio_base64, segment.duration);
                } else {
                    console.warn("Received neuro_speech_segment message with missing audio/text/duration:", segment);
                }
                break;
            case 'neuro_error_signal':
                console.warn("Received neuro_error_signal from backend.");
                showNeuroCaption("Someone tell Vedal there is a problem with my AI.");
                this.audioPlayer.playErrorSound();
                break;
            case 'chat_message':
                if (!this.chatSidebar.getIsCollapsed() || (message as ChatMessage).is_user_message) {
                   this.chatDisplay.appendChatMessage(message as ChatMessage);
                }
                break;
            case 'error':
                this.chatDisplay.appendChatMessage({ type: "chat_message", username: "System", text: `后端错误: ${(message as any).message}`, is_user_message: false });
                break;
        }
    }

    private isOutputPack(message: any): boolean {
        return (
            typeof message === 'object' &&
            message.type &&
            message.timestamp !== undefined &&
            message.payload !== undefined &&
            message.metadata &&
            message.metadata.agent_type &&
            message.metadata.source_tool
        );
    }

    private handleOutputPack(outputPack: any): void {
        switch (outputPack.type) {
            case 'speak':
                this.handleSpeakOutput(outputPack);
                break;
            case 'model_action':
                this.handleModelActionOutput(outputPack);
                break;
            case 'chat_message':
                this.handleChatMessageOutput(outputPack);
                break;
            default:
                console.warn("Unknown output pack type:", outputPack.type, outputPack);
                break;
        }
    }

    private handleSpeakOutput(outputPack: any): void {
        const payload = outputPack.payload;
        if (payload.audio_base64 && payload.text && typeof payload.duration === 'number') {
            // First, send the audio segment
            const segmentMessage: NeuroSpeechSegmentMessage = {
                type: "neuro_speech_segment",
                text: payload.text,
                audio_base64: payload.audio_base64,
                duration: payload.duration,
                is_end: false // Send as non-ending segment first
            };
            this.handleWebSocketMessage(segmentMessage);

            // Then, send the end signal
            const endMessage: NeuroSpeechSegmentMessage = {
                type: "neuro_speech_segment",
                is_end: true // Now mark as end
            };
            this.handleWebSocketMessage(endMessage);
        } else if (payload.text) {
            // Text-only speak (no audio)
            const segmentMessage: NeuroSpeechSegmentMessage = {
                type: "neuro_speech_segment",
                text: payload.text,
                is_end: false // Send as non-ending segment first
            };
            this.handleWebSocketMessage(segmentMessage);

            // Then, send the end signal
            const endMessage: NeuroSpeechSegmentMessage = {
                type: "neuro_speech_segment",
                is_end: true // Now mark as end
            };
            this.handleWebSocketMessage(endMessage);
        }
    }

    private handleModelActionOutput(outputPack: any): void {
        const payload = outputPack.payload;
        if (payload.action === 'spin') {
            this.neuroAvatar.triggerSpin();
        } else if (payload.action === 'zoom') {
            this.neuroAvatar.triggerZoom();
        } else {
            console.warn("Unknown model action:", payload.action);
        }
    }

    private handleChatMessageOutput(outputPack: any): void {
        const payload = outputPack.payload;
        const chatMessage: ChatMessage = {
            type: "chat_message",
            username: payload.username || 'Chatbot',
            text: payload.text || '',
            is_user_message: false
        };
        if (!this.chatSidebar.getIsCollapsed()) {
            this.chatDisplay.appendChatMessage(chatMessage);
        }
    }
    
    private sendUserMessage(payload: MessagePayload): void {
        const message = {
            username: this.currentSettings.username,
            ...payload
        };
        if (this.wsClient) {
            this.wsClient.send(message);
        } else {
            console.warn("Cannot send message: WebSocket client is not initialized.");
        }
    }

    private showStreamContent(): void {
        const streamArea = document.getElementById('stream-display-area');
        if (streamArea) {
            streamArea.style.visibility = 'visible';
            streamArea.style.opacity = '1';
        }
    }

    private hideStreamContent(): void {
        const streamArea = document.getElementById('stream-display-area');
        if (streamArea) {
            streamArea.style.visibility = 'hidden';
            streamArea.style.opacity = '0';
        }
    }
}
