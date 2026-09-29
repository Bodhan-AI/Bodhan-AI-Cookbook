package ai.bodhan.saathi.network

import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody

class BodhanException(message: String, cause: Throwable? = null) : Exception(message, cause)

class BodhanRepository(private val api: BodhanApiService) {

    private fun authHeader(apiKey: String) = "Bearer $apiKey"

    suspend fun transcribe(apiKey: String, wavFile: File, languageCode: String): String =
        withContext(Dispatchers.IO) {
            try {
                val fileBody = wavFile.asRequestBody("audio/wav".toMediaTypeOrNull())
                val filePart = MultipartBody.Part.createFormData("file", wavFile.name, fileBody)
                val textType = "text/plain".toMediaTypeOrNull()
                val modelPart = "indic-transcribe".toRequestBody(textType)
                val langPart = languageCode.toRequestBody(textType)
                val vadPart = "true".toRequestBody(textType)
                api.transcribe(authHeader(apiKey), filePart, modelPart, langPart, vadPart).text
            } catch (e: Exception) {
                throw BodhanException("Couldn't understand the audio. ${friendlyMessage(e)}", e)
            }
        }

    suspend fun translate(apiKey: String, text: String, sourceLang: String, targetLang: String): String =
        withContext(Dispatchers.IO) {
            try {
                val request = TranslateRequest(
                    messages = listOf(TranslateMessage(content = text)),
                    source_language_code = sourceLang,
                    target_language_code = targetLang,
                )
                val response = api.translate(authHeader(apiKey), request)
                response.choices.firstOrNull()?.message?.content?.trim()
                    ?: throw BodhanException("Translation came back empty.")
            } catch (e: BodhanException) {
                throw e
            } catch (e: Exception) {
                throw BodhanException("Couldn't translate the text. ${friendlyMessage(e)}", e)
            }
        }

    suspend fun speak(apiKey: String, text: String, languageCode: String, voice: String, outputFile: File): File =
        withContext(Dispatchers.IO) {
            try {
                val instructions = "{\"lang\": \"$languageCode\"}"
                val request = SpeechRequest(input = text, voice = voice, instructions = instructions)
                val response = api.speak(authHeader(apiKey), request)
                outputFile.outputStream().use { out -> response.byteStream().use { it.copyTo(out) } }
                outputFile
            } catch (e: Exception) {
                throw BodhanException("Couldn't generate the spoken audio. ${friendlyMessage(e)}", e)
            }
        }

    private fun friendlyMessage(e: Exception): String = when {
        e is retrofit2.HttpException && e.code() == 401 -> "Check that your API key is correct."
        e is retrofit2.HttpException -> "Server said: ${e.code()}."
        e.message != null -> e.message!!
        else -> "Please check your internet connection and try again."
    }
}
