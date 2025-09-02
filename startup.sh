apt-get update && apt-get install -y unixodbc-dev

curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add -
curl https://packages.microsoft.com/config/debian/11/prod.list | tee /etc/apt/sources.list.d/mssql-release.list
apt-get update

ACCEPT_EULA=Y apt-get install -y msodbcsql17
