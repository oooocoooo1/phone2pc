package io.github.oooocoooo1.phone2pc

import android.content.Intent
import android.os.Build
import io.flutter.app.FlutterApplication
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.embedding.engine.FlutterEngineCache
import io.flutter.embedding.engine.dart.DartExecutor
import io.flutter.plugin.common.MethodChannel

class Phone2PCApplication : FlutterApplication() {
    override fun onCreate() {
        super.onCreate()
        if (FlutterEngineCache.getInstance().get(ENGINE_ID) != null) return

        val engine = FlutterEngine(this)
        FlutterEngineCache.getInstance().put(ENGINE_ID, engine)
        MethodChannel(engine.dartExecutor.binaryMessenger, KEEP_ALIVE_CHANNEL)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "start" -> {
                        val host = call.argument<String>("host")?.trim().orEmpty()
                        val intent = Intent(this, ConnectionKeepAliveService::class.java).apply {
                            action = ConnectionKeepAliveService.ACTION_START
                            putExtra(ConnectionKeepAliveService.EXTRA_HOST, host)
                        }
                        try {
                            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                                startForegroundService(intent)
                            } else {
                                startService(intent)
                            }
                            result.success(true)
                        } catch (error: RuntimeException) {
                            result.error("KEEP_ALIVE_START_FAILED", error.message, null)
                        }
                    }
                    "stop" -> {
                        ConnectionKeepAliveService.clearRememberedHost(this)
                        stopService(Intent(this, ConnectionKeepAliveService::class.java))
                        result.success(true)
                    }
                    else -> result.notImplemented()
                }
            }
        engine.dartExecutor.executeDartEntrypoint(
            DartExecutor.DartEntrypoint.createDefault(),
        )
    }

    companion object {
        const val ENGINE_ID = "phone2pc_engine"
        const val KEEP_ALIVE_CHANNEL = "io.github.oooocoooo1.phone2pc/keep_alive"
    }
}
