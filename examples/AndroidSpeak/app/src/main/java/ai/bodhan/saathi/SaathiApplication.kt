package ai.bodhan.saathi

import android.app.Application
import ai.bodhan.saathi.data.SettingsRepository
import ai.bodhan.saathi.network.BodhanClient
import ai.bodhan.saathi.network.BodhanRepository

class SaathiApplication : Application() {

    lateinit var settingsRepository: SettingsRepository
        private set

    val bodhanRepository: BodhanRepository by lazy { BodhanRepository(BodhanClient.create()) }

    override fun onCreate() {
        super.onCreate()
        settingsRepository = SettingsRepository(applicationContext)
    }
}
