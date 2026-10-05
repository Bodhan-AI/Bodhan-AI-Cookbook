package ai.bodhan.saathi.data

import android.content.Context
import android.content.SharedPreferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.dataStore by preferencesDataStore(name = "saathi_settings")

enum class ApiModel { TRANSCRIBE, TRANSLATE, SPEAK }

data class AppSettings(
    val myLanguage: String = "en",
    val localLanguage: String = "ta",
    val voice: String = "Amit",
    val autoPlay: Boolean = true,
)

class SettingsRepository(private val appContext: Context) {

    private val securePrefs: SharedPreferences by lazy {
        val masterKey = MasterKey.Builder(appContext)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            appContext,
            "saathi_secure_prefs",
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    fun getApiKey(model: ApiModel): String? = securePrefs.getString(prefNameFor(model), null)

    fun setApiKey(model: ApiModel, key: String) {
        securePrefs.edit().putString(prefNameFor(model), key).apply()
    }

    fun clearApiKey(model: ApiModel) {
        securePrefs.edit().remove(prefNameFor(model)).apply()
    }

    fun hasAllApiKeys(): Boolean = ApiModel.entries.all { !getApiKey(it).isNullOrBlank() }

    private fun prefNameFor(model: ApiModel) = "bodhan_api_key_${model.name.lowercase()}"

    val settingsFlow: Flow<AppSettings> = appContext.dataStore.data.map { prefs ->
        AppSettings(
            myLanguage = prefs[MY_LANGUAGE] ?: "en",
            localLanguage = prefs[LOCAL_LANGUAGE] ?: "ta",
            voice = prefs[VOICE] ?: "Amit",
            autoPlay = prefs[AUTO_PLAY] ?: true,
        )
    }

    suspend fun setMyLanguage(code: String) {
        appContext.dataStore.edit { it[MY_LANGUAGE] = code }
    }

    suspend fun setLocalLanguage(code: String) {
        appContext.dataStore.edit { it[LOCAL_LANGUAGE] = code }
    }

    suspend fun setVoice(voice: String) {
        appContext.dataStore.edit { it[VOICE] = voice }
    }

    suspend fun setAutoPlay(enabled: Boolean) {
        appContext.dataStore.edit { it[AUTO_PLAY] = enabled }
    }

    companion object {
        private val MY_LANGUAGE = stringPreferencesKey("my_language")
        private val LOCAL_LANGUAGE = stringPreferencesKey("local_language")
        private val VOICE = stringPreferencesKey("voice")
        private val AUTO_PLAY = booleanPreferencesKey("auto_play")
    }
}
