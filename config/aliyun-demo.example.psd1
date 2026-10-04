@{
  # Copy this file to aliyun-demo.local.psd1 and fill the local-only values.
  # Never commit the local file. The startup script never prints these values.
  AliyunAppKey = ''
  AliyunAccessKeyId = ''
  AliyunAccessKeySecret = ''

  # The database must already exist. Flyway creates and updates its tables.
  MysqlUrl = 'jdbc:mysql://127.0.0.1:3306/uav_usv_platform?useUnicode=true&characterEncoding=utf8&serverTimezone=Asia/Shanghai&useSSL=false&allowPublicKeyRetrieval=true'
  MysqlUsername = 'root'
  MysqlPassword = ''

  BootstrapAdminUsername = 'admin'
  BootstrapAdminPassword = '123456'

  # Optional but recommended: keep one stable 32-byte Base64 key per machine.
  # If blank, the startup script generates an in-memory key for that run.
  ResultEncryptionKey = ''

  # Leave blank to discover the executables from PATH and common local paths.
  PythonPath = ''
  FfmpegPath = ''

  BackendPort = 8081
  FrontendPort = 5174
  AliyunRegion = 'cn-shanghai'
}
