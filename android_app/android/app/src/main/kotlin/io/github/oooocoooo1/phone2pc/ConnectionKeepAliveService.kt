package io.github.oooocoooo1.phone2pc

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.Context
import android.net.wifi.WifiManager
import android.os.IBinder
import io.flutter.embedding.engine.FlutterEngineCache
import io.flutter.plugin.common.MethodChannel

class ConnectionKeepAliveService : Service() {
    private var wifiLock: WifiManager.WifiLock? = null

    override fun onCreate() {
        super.onCreate()
        val wifiManager = applicationContext.getSystemService(WIFI_SERVICE) as WifiManager
        @Suppress("DEPRECATION")
        wifiLock = wifiManager.createWifiLock(
            WifiManager.WIFI_MODE_FULL_HIGH_PERF,
            "$packageName:connection",
        ).apply {
            setReferenceCounted(false)
            acquire()
        }
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                "连接常驻",
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = "保持 Phone2PC 与电脑的后台连接"
                setShowBadge(false)
            },
        )
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_DISCONNECT -> {
                clearRememberedHost(this)
                FlutterEngineCache.getInstance()
                    .get(Phone2PCApplication.ENGINE_ID)
                    ?.let { engine ->
                        MethodChannel(engine.dartExecutor.binaryMessenger, CONTROL_CHANNEL)
                            .invokeMethod("disconnectRequested", null)
                    }
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
                return START_NOT_STICKY
            }
        }

        val preferences = getSharedPreferences(PREFERENCES, MODE_PRIVATE)
        val requestedHost = intent?.getStringExtra(EXTRA_HOST)?.trim().orEmpty()
        val host = requestedHost.ifEmpty {
            preferences.getString(KEY_HOST, "").orEmpty()
        }
        if (requestedHost.isNotEmpty()) {
            preferences.edit().putString(KEY_HOST, requestedHost).apply()
        }
        startForeground(NOTIFICATION_ID, buildNotification(host))
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        wifiLock?.let { lock ->
            if (lock.isHeld) lock.release()
        }
        wifiLock = null
        super.onDestroy()
    }

    private fun buildNotification(host: String): Notification {
        val openIntent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val openPendingIntent = PendingIntent.getActivity(
            this,
            0,
            openIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val disconnectIntent = Intent(this, ConnectionKeepAliveService::class.java).apply {
            action = ACTION_DISCONNECT
        }
        val disconnectPendingIntent = PendingIntent.getService(
            this,
            1,
            disconnectIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val status = if (host.isEmpty()) {
            "正在恢复与电脑的连接"
        } else {
            "保持连接：$host"
        }
        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("Phone2PC 后台常驻")
            .setContentText(status)
            .setContentIntent(openPendingIntent)
            .setCategory(Notification.CATEGORY_SERVICE)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setShowWhen(false)
            .addAction(0, "断开", disconnectPendingIntent)
            .build()
    }

    companion object {
        const val ACTION_START = "io.github.oooocoooo1.phone2pc.START_KEEP_ALIVE"
        const val ACTION_STOP = "io.github.oooocoooo1.phone2pc.STOP_KEEP_ALIVE"
        const val ACTION_DISCONNECT = "io.github.oooocoooo1.phone2pc.DISCONNECT"
        const val EXTRA_HOST = "host"
        const val CONTROL_CHANNEL = "io.github.oooocoooo1.phone2pc/connection_control"
        private const val CHANNEL_ID = "phone2pc_connection"
        private const val NOTIFICATION_ID = 8765
        private const val PREFERENCES = "connection_service"
        private const val KEY_HOST = "last_host"

        fun clearRememberedHost(context: Context) {
            context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
                .edit()
                .remove(KEY_HOST)
                .apply()
        }
    }
}
