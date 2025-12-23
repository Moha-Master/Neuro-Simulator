import yaml
import asyncio
from typing import Any, List, Optional, Literal
from pydantic import BaseModel, Field, field_validator

# --- Provider Models ---


class LLMProviderSettings(BaseModel):
    """Settings for a single LLM provider."""

    provider_id: str = Field(..., title="Provider ID", description="Automatic generated unique identifier for this provider instance.")
    display_name: str = Field(..., title="Display Name", description="User-friendly name for this provider.")
    provider_type: Literal["openai", "gemini"] = Field(..., title="Provider Type", description="The type of the provider service.")
    api_key: Optional[str] = Field(default=None, title="API Key", description="API key for authentication.")
    base_url: Optional[str] = Field(default=None, title="Base URL", description="Optional custom base URL for the API endpoint.")
    model_name: str = Field(..., title="Model Name", description="The specific model to be used (e.g., gpt-4, gemini-pro).")
    force_json_output: bool = Field(True, title="Force JSON Output", description="Ensures the model's output is always a valid JSON object, improving reliability for tool calls.")
    temperature: Optional[float] = Field(default=1.2, ge=0.0, le=2.0, title="Temperature", description="Controls randomness. Higher values (e.g., 1.2) make output more random, lower values (e.g., 0.7) make it more deterministic.")
    top_p: Optional[float] = Field(default=None, ge=0.0, le=1.0, title="Top P", description="Nucleus sampling: model considers results of tokens with this probability mass. (e.g., 0.1 means only tokens comprising the top 10% probability mass are considered).")
    top_k: Optional[int] = Field(default=None, ge=0, title="Top K", description="Model considers the top K most likely tokens at each step.")
    frequency_penalty: Optional[float] = Field(default=0.5, ge=-2.0, le=2.0, title="Frequency Penalty", description="Positive values penalize new tokens based on their existing frequency, decreasing repetition. (OpenAI/Gemini)")
    presence_penalty: Optional[float] = Field(default=0.5, ge=-2.0, le=2.0, title="Presence Penalty", description="Positive values penalize new tokens based on whether they appear in the text so far, increasing the model's likelihood to talk about new topics. (OpenAI/Gemini)")
    max_output_tokens: Optional[int] = Field(default=2048, ge=1, title="Max Output Tokens", description="The maximum number of tokens to generate in the response.")
    stop_sequences: Optional[List[str]] = Field(default=None, title="Stop Sequences", description="A list of strings that will cause the model to stop generating text if they are produced.")
    seed: Optional[int] = Field(default=None, title="Seed", description="A seed for sampling to create reproducible results. (Useful for testing).")


class TTSProviderSettings(BaseModel):
    """Settings for a single Text-to-Speech (TTS) provider."""

    provider_id: str = Field(..., title="Provider ID", description="Automatic generated unique identifier for this provider instance.")
    display_name: str = Field(..., title="Display Name", description="User-friendly name for this provider.")
    provider_type: Literal["azure"] = Field(..., title="Provider Type", description="The type of the provider service.")
    api_key: Optional[str] = Field(default=None, title="API Key", description="API key for authentication.")
    region: Optional[str] = Field(default=None, title="Region", description="The service region for the provider (e.g., eastus).")
    tts_timeout: float = Field(10.0, title="TTS Timeout (sec)", description="Timeout in seconds for the TTS synthesis request.")


# --- Core Application Settings Models ---


class NeuroSettings(BaseModel):
    """Settings for the main agent (Neuro)."""

    neuro_llm_provider_id: Optional[str] = Field(default=None, title="Neuro LLM Provider ID", description="The ID of the LLM provider for Neuro's main responses.")
    tts_provider_id: Optional[str] = Field(default=None, title="TTS Provider ID", description="The ID of the TTS provider for speech synthesis.")
    input_chat_sample_size: int = Field(10, title="Input Chat Sample Size", description="Number of recent chat messages to use as context in one turn.")
    post_speech_cooldown_sec: List[float] = Field(default_factory=lambda: [1.0, 3.0], title="Post-Speech Cooldown Range (sec)", description="The min and max time to wait after a speech segment. A random value in this range is chosen. Set both to the same value for a fixed delay.")
    initial_greeting: str = Field("The stream has just started. Greet your audience and say hello!", title="Initial Greeting", format="text-area", description="The message Neuro will see when the stream first starts.")  # type: ignore[call-overload]
    neuro_input_queue_max_size: int = Field(200, title="Neuro Input Queue Max Size", description="Max number of incoming events (chats, etc.) to hold in the queue.")
    recent_history_lines: int = Field(10, title="Recent History Lines", description="Number of recent spoken lines to include in the prompt context.")
    filter_keywords: List[str] = Field(default_factory=list, title="Filter Keywords", description="List of keywords that will be filtered out from Neuro's speech. Case-insensitive. Leave empty to disable filtering.")

    @field_validator('post_speech_cooldown_sec', mode='before')
    @classmethod
    def _migrate_cooldown_format(cls, v: Any) -> Any:
        if isinstance(v, (float, int)):
            return [float(v), float(v)]
        return v


class ChatbotSettings(BaseModel):
    """Settings for the audience chatbot."""

    chatbot_llm_provider_id: Optional[str] = Field(default=None, title="Chatbot LLM Provider ID", description="The ID of the LLM provider for the audience-facing chatbot.")
    generation_interval_sec: int = Field(3, title="Generation Interval (sec)", description="How often (in seconds) the chatbot should try to generate a message.")
    chats_per_batch: int = Field(4, title="Chats per batch", description="How many chat messages the chatbot should generate at once.")
    initial_prompt: str = Field(default="The stream is starting! Let's say hello and get the hype going!", title="Initial Prompt", format="text-area", description="The message the chatbot will use as context before Neuro has spoken.")  # type: ignore[call-overload]


class StreamSettings(BaseModel):
    """Settings related to the stream's appearance."""

    streamer_nickname: str = Field("vedal987", title="Streamer Nickname", description="The nickname of the streamer.")
    stream_title: str = Field("neuro-sama is here for u all", title="Stream Title", description="The title of the stream.")
    stream_category: str = Field("谈天说地", title="Stream Category", description="The category or game being streamed.")
    stream_tags: List[str] = Field(default_factory=lambda: ["Vtuber", "AI", "Cute", "English", "Gremlin", "catgirl"], title="Stream Tags", description="Tags to associate with the stream.")


class ServerSettings(BaseModel):
    """Settings for the web server and performance."""

    host: str = Field("127.0.0.1", title="Host", description="The IP address the server will bind to.")
    port: int = Field(8000, title="Port", description="The port the server will listen on.")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field("INFO", title="Log Level", description="The minimum level of logs to display.")
    panel_password: Optional[str] = Field("your-secret-api-token-here", title="Panel Password", format="password", description="Password to protect access to the admin dashboard.")  # type: ignore[call-overload]
    client_origins: List[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"], title="Client Origins", description="Allowed origins for CORS (whitelist for remote dashboard).")
    audience_chat_buffer_max_size: int = Field(1000, title="Audience Chat Buffer Max Size", description="How many recent chat messages to keep in buffer for new clients.")
    initial_chat_backlog_limit: int = Field(50, title="Initial Chat Backlog Limit", description="How many messages from buffer to send to a new client.")


class AppSettings(BaseModel):
    """Root model for all application settings."""

    llm_providers: List[LLMProviderSettings] = Field(default_factory=list, title="LLM Providers", description="Configuration for Large Language Model providers.")
    tts_providers: List[TTSProviderSettings] = Field(default_factory=list, title="TTS Providers", description="Configuration for Text-to-Speech providers.")
    neuro: NeuroSettings = Field(default_factory=NeuroSettings, title="Neuro", description="Settings for Neuro Agent.")
    chatbot: ChatbotSettings = Field(default_factory=ChatbotSettings, title="Chatbot", description="Settings for Chatbot Agent.")
    stream: StreamSettings = Field(default_factory=StreamSettings, title="Stream", description="Settings related to the stream's appearance.")
    server: ServerSettings = Field(default_factory=ServerSettings, title="Server", description="Settings for the server and performance.")


# --- Configuration Manager ---


class ConfigManager:
    """Manages loading, saving, and updating the application settings."""

    def __init__(self):
        self.file_path: Optional[str] = None
        self.settings: Optional[AppSettings] = None
        self.update_callbacks = []

    def load(self, file_path: str):
        self.file_path = file_path
        try:
            with open(self.file_path, "r") as f:
                data = yaml.safe_load(f) or {}
        except FileNotFoundError:
            data = {}
        
        self.settings = AppSettings.model_validate(data)
        # Save the settings back immediately. This auto-migrates old configs
        # and populates new ones with all default values.
        self.save_settings()

    def save_settings(self):
        if self.settings and self.file_path:
            with open(self.file_path, "w") as f:
                yaml.dump(
                    self.settings.model_dump(exclude_none=True), f, sort_keys=False
                )

    def reset_to_defaults(self):
        """Resets the settings to their default values while preserving providers."""
        if self.file_path and self.settings:
            # Preserve the existing provider configurations
            preserved_llm_providers = self.settings.llm_providers
            preserved_tts_providers = self.settings.tts_providers

            # Create a new AppSettings instance with all other values at their defaults
            new_default_settings = AppSettings()

            # Overwrite the default (empty) provider lists with the preserved ones
            new_default_settings.llm_providers = preserved_llm_providers
            new_default_settings.tts_providers = preserved_tts_providers

            # Set the current settings to this new, combined configuration
            self.settings = new_default_settings
            
            # Save the result
            self.save_settings()

    def get_settings_schema(self):
        return AppSettings.model_json_schema()

    async def update_settings(self, updated_data: dict):
        if self.settings:
            updated_model_dict = self.settings.model_dump()
            updated_model_dict.update(updated_data)

            self.settings = AppSettings.model_validate(updated_model_dict)
            self.save_settings()
            for callback in self.update_callbacks:
                if asyncio.iscoroutinefunction(callback):
                    await callback(self.settings)
                else:
                    callback(self.settings)

    def register_update_callback(self, callback):
        self.update_callbacks.append(callback)


config_manager = ConfigManager()
