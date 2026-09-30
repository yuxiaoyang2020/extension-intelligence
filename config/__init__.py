import pymysql

# Django 官方支持 mysqlclient，但那个库在 Mac 上经常要编译本地依赖，装起来麻烦。
# PyMySQL 是纯 Python 实现，免编译，装上之后伪装成 MySQLdb 给 Django 用，效果一样。
pymysql.install_as_MySQLdb()
