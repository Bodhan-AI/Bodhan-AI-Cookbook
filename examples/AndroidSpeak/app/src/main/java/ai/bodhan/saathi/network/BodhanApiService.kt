package ai.bodhan.saathi.network

import okhttp3.MultipartBody
import okhttp3.RequestBody
import okhttp3.ResponseBody
import retrofit2.http.Body
import retrofit2.http.Header
import retrofit2.http.Multipart
import retrofit2.http.POST
import retrofit2.http.Part

const val BODHAN_BASE_URL = "https://api.bodhan.ai/"

data class TranscriptionResponse(
    val text: String,
)

data class TranslateMessage(
    val role: String = "user",
    val content: String,
)

data class TranslateRequest(
    val model: String = "indic-translate",
    val messages: List<TranslateMessage>,
    val source_language_code: String,
    val target_language_code: String,
)

data class ChatChoice(
    val message: TranslateMessage,
)

data class ChatCompletionResponse(
    val choices: List<ChatChoice>,
)

data class SpeechRequest(
    val model: String = "indic-speak",
    val input: String,
    val voice: String,
    val instructions: String,
)

interface BodhanApiService {

    @Multipart
    @POST("v1/audio/transcriptions")
    suspend fun transcribe(
        @Header("Authorization") authorization: String,
        @Part file: MultipartBody.Part,
        @Part("model") model: RequestBody,
        @Part("language") language: RequestBody,
        @Part("vad") vad: RequestBody,
    ): TranscriptionResponse

    @POST("v1/chat/completions")
    suspend fun translate(
        @Header("Authorization") authorization: String,
        @Body request: TranslateRequest,
    ): ChatCompletionResponse

    @POST("v1/audio/speech")
    suspend fun speak(
        @Header("Authorization") authorization: String,
        @Body request: SpeechRequest,
    ): ResponseBody
}
