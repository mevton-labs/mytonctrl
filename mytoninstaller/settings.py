import os
import os.path
import psutil
import base64
import subprocess
import requests
import random
import json
import pkg_resources

from mypylib.mypylib import (
	add2systemd,
	get_dir_from_path,
	run_as_root,
	color_print,
	ip2int,
	Dict
)
from mytoninstaller.utils import StartValidator, StartMytoncore, start_service, stop_service, get_ed25519_pubkey
from mytoninstaller.config import SetConfig, GetConfig, get_own_ip, backup_config
from mytoncore.utils import hex2b64


def FirstNodeSettings(local):
	local.add_log("start FirstNodeSettings fuction", "debug")

	# Создать переменные
	user = local.buffer.user
	vuser = local.buffer.vuser
	ton_work_dir = local.buffer.ton_work_dir
	ton_db_dir = local.buffer.ton_db_dir
	keys_dir = local.buffer.keys_dir
	tonLogPath = local.buffer.ton_log_path
	validatorAppPath = local.buffer.validator_app_path
	globalConfigPath = local.buffer.global_config_path
	vconfig_path = local.buffer.vconfig_path

	if os.getenv('ARCHIVE_TTL'):
		archive_ttl = int(os.getenv('ARCHIVE_TTL'))
	else:
		archive_ttl = 2592000 if local.buffer.mode == 'liteserver' else 86400

	# Создать пользователя
	file = open("/etc/passwd", 'rt')
	text = file.read()
	file.close()
	if vuser not in text:
		local.add_log("Creating new user: " + vuser, "debug")
		args = ["/usr/sbin/useradd", "-d", "/dev/null", "-s", "/dev/null", vuser]
		subprocess.run(args)
	#end if

	# Прописать автозагрузку
	cpus = psutil.cpu_count() - 1
	cmd = f"{validatorAppPath} --threads {cpus} --daemonize --global-config {globalConfigPath} --db {ton_db_dir} --archive-ttl {archive_ttl} --verbosity 3"
	add2systemd(name="validator", user=vuser, start=cmd) # post="/usr/bin/python3 /usr/src/mytonctrl/mytoncore.py -e \"validator down\""
	if os.path.isfile("/etc/systemd/system/validator.service"):
		local.add_log(f"Add validator to systemd", "debug")
	else:
		local.add_log(f"Error: validator.service not found", "error")

	# Проверить конфигурацию
	if os.path.isfile(vconfig_path):
		local.add_log(f"Validators config '{vconfig_path}' already exist. Break FirstNodeSettings fuction", "warning")
		return
	#end if


	# Подготовить папки валидатора
	os.makedirs(ton_db_dir, exist_ok=True)
	os.makedirs(keys_dir, exist_ok=True)


	# Получить внешний ip адрес
	ip = get_own_ip()
	# get ip from env
	if os.getenv('PUBLIC_IP'):
		ip = os.getenv('PUBLIC_IP')
	vport = random.randint(2000, 65000)
	# get vport from env
	if os.getenv('VALIDATOR_PORT'):
		vport = os.getenv('VALIDATOR_PORT')
	addr = "{ip}:{vport}".format(ip=ip, vport=vport)
	local.add_log("Use addr: " + addr, "debug")

	# Первый запуск
	local.add_log("First start validator - create config.json", "debug")
	args = [validatorAppPath, "--global-config", globalConfigPath, "--db", ton_db_dir, "--ip", addr, "--logname", tonLogPath]
	process = subprocess.run(args)
	output = process.returncode
	error = process.stderr
	local.add_log(validatorAppPath + ": Exit code:{code}; Error: {error}".format(code=output, error=error), "debug")
	# Скачать дамп
	DownloadDump(local)

	# chown 1
	local.add_log("Chown ton-work dir", "debug")
	args = ["chown", "-R", vuser + ':' + vuser, ton_work_dir]
	subprocess.run(args)

	# start validator
	StartValidator(local)
#end define


def DownloadDump(local):
	dump = local.buffer.dump
	if dump == False:
		return
	#end if

	local.add_log("start DownloadDump fuction", "debug")
	url = "https://dump.ton.org"
	dumpSize = requests.get(url + "/dumps/latest.tar.size.archive.txt").text
	print("dumpSize:", dumpSize)
	needSpace = int(dumpSize) * 3
	diskSpace = psutil.disk_usage("/var")
	if needSpace > diskSpace.free:
		return
	#end if

	# apt install
	cmd = "apt install plzip pv curl -y"
	os.system(cmd)

	# download dump
	cmd = "curl -s {url}/dumps/latest.tar.lz | pv | plzip -d -n8 | tar -xC /var/ton-work/db".format(url=url)
	os.system(cmd)
#end define


def FirstMytoncoreSettings(local):
	local.add_log("start FirstMytoncoreSettings fuction", "debug")
	user = local.buffer.user

	# Прописать mytoncore.py в автозагрузку
	# add2systemd(name="mytoncore", user=user, start="/usr/bin/python3 /usr/src/mytonctrl/mytoncore.py")  # TODO: fix path
	add2systemd(name="mytoncore", user=user, start="/usr/bin/python3 -m mytoncore")

	# Проверить конфигурацию
	path = "/home/{user}/.local/share/mytoncore/mytoncore.db".format(user=user)
	if os.path.isfile(path):
		local.add_log(f"{path} already exist. Break FirstMytoncoreSettings fuction", "warning")
		return
	#end if

	path2 = "/usr/local/bin/mytoncore/mytoncore.db"
	if os.path.isfile(path2):
		local.add_log(f"{path2}.db already exist. Break FirstMytoncoreSettings fuction", "warning")
		return
	#end if

	#amazon bugfix
	path1 = "/home/{user}/.local/".format(user=user)
	path2 = path1 + "share/"
	chownOwner = "{user}:{user}".format(user=user)
	os.makedirs(path1, exist_ok=True)
	os.makedirs(path2, exist_ok=True)
	args = ["chown", chownOwner, path1, path2]
	subprocess.run(args)

	# Подготовить папку mytoncore
	mconfig_path = local.buffer.mconfig_path
	mconfigDir = get_dir_from_path(mconfig_path)
	os.makedirs(mconfigDir, exist_ok=True)

	# create variables
	src_dir = local.buffer.src_dir
	ton_bin_dir = local.buffer.ton_bin_dir
	ton_src_dir = local.buffer.ton_src_dir

	# general config
	mconfig = Dict()
	mconfig.config = Dict()
	mconfig.config.logLevel = "debug"
	mconfig.config.isLocaldbSaving = True

	# fift
	fift = Dict()
	fift.appPath = ton_bin_dir + "crypto/fift"
	fift.libsPath = ton_src_dir + "crypto/fift/lib"
	fift.smartcontsPath = ton_src_dir + "crypto/smartcont"
	mconfig.fift = fift

	# lite-client
	liteClient = Dict()
	liteClient.appPath = ton_bin_dir + "lite-client/lite-client"
	liteClient.configPath = ton_bin_dir + "global.config.json"
	mconfig.liteClient = liteClient

	# Telemetry
	mconfig.sendTelemetry = local.buffer.telemetry

	# Записать настройки в файл
	SetConfig(path=mconfig_path, data=mconfig)

	# chown 1
	args = ["chown", user + ':' + user, mconfigDir, mconfig_path]
	subprocess.run(args)

	# start mytoncore
	StartMytoncore(local)
#end define

def EnableValidatorConsole(local):
	local.add_log("start EnableValidatorConsole function", "debug")

	# Create variables
	user = local.buffer.user
	vuser = local.buffer.vuser
	cport = local.buffer.cport
	src_dir = local.buffer.src_dir
	ton_db_dir = local.buffer.ton_db_dir
	ton_bin_dir = local.buffer.ton_bin_dir
	vconfig_path = local.buffer.vconfig_path
	generate_random_id = ton_bin_dir + "utils/generate-random-id"
	keys_dir = local.buffer.keys_dir
	client_key = keys_dir + "client"
	server_key = keys_dir + "server"
	client_pubkey = client_key + ".pub"
	server_pubkey = server_key + ".pub"

	# Check if key exist
	if os.path.isfile(server_key):
		local.add_log(f"Server key '{server_key}' already exist. Break EnableValidatorConsole fuction", "warning")
		return
	#end if

	if os.path.isfile(client_key):
		local.add_log(f"Client key '{client_key}' already exist. Break EnableValidatorConsole fuction", "warning")
		return
	#end if

	# generate server key
	args = [generate_random_id, "--mode", "keys", "--name", server_key]
	process = subprocess.run(args, stdout=subprocess.PIPE)
	output = process.stdout.decode("utf-8")
	output_arr = output.split(' ')
	server_key_hex = output_arr[0]
	server_key_b64 = output_arr[1].replace('\n', '')

	# move key
	newKeyPath = ton_db_dir + "/keyring/" + server_key_hex
	args = ["mv", server_key, newKeyPath]
	subprocess.run(args)

	# generate client key
	args = [generate_random_id, "--mode", "keys", "--name", client_key]
	process = subprocess.run(args, stdout=subprocess.PIPE)
	output = process.stdout.decode("utf-8")
	output_arr = output.split(' ')
	client_key_hex = output_arr[0]
	client_key_b64 = output_arr[1].replace('\n', '')

	# chown 1
	args = ["chown", vuser + ':' + vuser, newKeyPath]
	subprocess.run(args)

	# chown 2
	args = ["chown", user + ':' + user, server_pubkey, client_key, client_pubkey]
	subprocess.run(args)

	# read vconfig
	vconfig = GetConfig(path=vconfig_path)

	# prepare config
	control = Dict()
	control.id = server_key_b64
	control.port = cport
	allowed = Dict()
	allowed.id = client_key_b64
	allowed.permissions = 15
	control.allowed = [allowed] # fix me
	vconfig.control.append(control)

	# write vconfig
	SetConfig(path=vconfig_path, data=vconfig)

	# restart validator
	StartValidator(local)

	# read mconfig
	mconfig_path = local.buffer.mconfig_path
	mconfig = GetConfig(path=mconfig_path)

	# edit mytoncore config file
	validatorConsole = Dict()
	validatorConsole.appPath = ton_bin_dir + "validator-engine-console/validator-engine-console"
	validatorConsole.privKeyPath = client_key
	validatorConsole.pubKeyPath = server_pubkey
	validatorConsole.addr = "127.0.0.1:{cport}".format(cport=cport)
	mconfig.validatorConsole = validatorConsole

	# write mconfig
	SetConfig(path=mconfig_path, data=mconfig)

	# Подтянуть событие в mytoncore.py
	# cmd = "python3 {srcDir}mytonctrl/mytoncore.py -e \"enableVC\"".format(srcDir=srcDir)
	cmd = 'python3 -m mytoncore -e "enableVC"'
	args = ["su", "-l", user, "-c", cmd]
	subprocess.run(args)

	# restart mytoncore
	StartMytoncore(local)
#end define

def EnableLiteServer(local):
	local.add_log("start EnableLiteServer function", "debug")

	# Create variables
	user = local.buffer.user
	vuser = local.buffer.vuser
	lport = local.buffer.lport
	src_dir = local.buffer.src_dir
	ton_db_dir = local.buffer.ton_db_dir
	keys_dir = local.buffer.keys_dir
	ton_bin_dir = local.buffer.ton_bin_dir
	vconfig_path = local.buffer.vconfig_path
	generate_random_id = ton_bin_dir + "utils/generate-random-id"
	liteserver_key = keys_dir + "liteserver"
	liteserver_pubkey = liteserver_key + ".pub"

	# Check if key exist
	if os.path.isfile(liteserver_pubkey):
		local.add_log(f"Liteserver key '{liteserver_pubkey}' already exist. Break EnableLiteServer fuction", "warning")
		return
	#end if

	# generate liteserver key
	local.add_log("generate liteserver key", "debug")
	args = [generate_random_id, "--mode", "keys", "--name", liteserver_key]
	process = subprocess.run(args, stdout=subprocess.PIPE)
	output = process.stdout.decode("utf-8")
	output_arr = output.split(' ')
	liteserver_key_hex = output_arr[0]
	liteserver_key_b64 = output_arr[1].replace('\n', '')

	# move key
	local.add_log("move key", "debug")
	newKeyPath = ton_db_dir + "/keyring/" + liteserver_key_hex
	args = ["mv", liteserver_key, newKeyPath]
	subprocess.run(args)

	# chown 1
	local.add_log("chown 1", "debug")
	args = ["chown", vuser + ':' + vuser, newKeyPath]
	subprocess.run(args)

	# chown 2
	local.add_log("chown 2", "debug")
	args = ["chown", user + ':' + user, liteserver_pubkey]
	subprocess.run(args)

	# read vconfig
	local.add_log("read vconfig", "debug")
	vconfig = GetConfig(path=vconfig_path)

	# prepare vconfig
	local.add_log("prepare vconfig", "debug")
	liteserver = Dict()
	liteserver.id = liteserver_key_b64
	liteserver.port = lport
	vconfig.liteservers.append(liteserver)

	# write vconfig
	local.add_log("write vconfig", "debug")
	SetConfig(path=vconfig_path, data=vconfig)

	# restart validator
	StartValidator(local)

	# edit mytoncore config file
	# read mconfig
	local.add_log("read mconfig", "debug")
	mconfig_path = local.buffer.mconfig_path
	mconfig = GetConfig(path=mconfig_path)

	# edit mytoncore config file
	local.add_log("edit mytoncore config file", "debug")
	liteServer = Dict()
	liteServer.pubkeyPath = liteserver_pubkey
	liteServer.ip = "127.0.0.1"
	liteServer.port = lport
	mconfig.liteClient.liteServer = liteServer

	# write mconfig
	local.add_log("write mconfig", "debug")
	SetConfig(path=mconfig_path, data=mconfig)

	# restart mytoncore
	StartMytoncore(local)
#end define


def EnableDhtServer(local):
	local.add_log("start EnableDhtServer function", "debug")
	vuser = local.buffer.vuser
	ton_bin_dir = local.buffer.ton_bin_dir
	globalConfigPath = local.buffer.global_config_path
	dht_server = ton_bin_dir + "dht-server/dht-server"
	generate_random_id = ton_bin_dir + "utils/generate-random-id"
	tonDhtServerDir = "/var/ton-dht-server/"
	tonDhtKeyringDir = tonDhtServerDir + "keyring/"

	# Проверить конфигурацию
	dht_config_path = "/var/ton-dht-server/config.json"
	if os.path.isfile(dht_config_path):
		local.add_log(f"DHT-Server '{dht_config_path}' already exist. Break EnableDhtServer fuction", "warning")
		return
	#end if

	# Подготовить папку
	os.makedirs(tonDhtServerDir, exist_ok=True)

	# Прописать автозагрузку
	cmd = "{dht_server} -C {globalConfigPath} -D {tonDhtServerDir}"
	cmd = cmd.format(dht_server=dht_server, globalConfigPath=globalConfigPath, tonDhtServerDir=tonDhtServerDir)
	add2systemd(name="dht-server", user=vuser, start=cmd)

	# Получить внешний ip адрес
	ip = get_own_ip()
	port = random.randint(2000, 65000)
	addr = "{ip}:{port}".format(ip=ip, port=port)

	# Первый запуск
	args = [dht_server, "-C", globalConfigPath, "-D", tonDhtServerDir, "-I", addr]
	subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

	# Получить вывод конфига
	key = os.listdir(tonDhtKeyringDir)[0]
	ip = ip2int(ip)
	text = '{"@type": "adnl.addressList", "addrs": [{"@type": "adnl.address.udp", "ip": ' + str(ip) + ', "port": ' + str(port) + '}], "version": 0, "reinit_date": 0, "priority": 0, "expire_at": 0}'
	args = [generate_random_id, "-m", "dht", "-k", tonDhtKeyringDir + key, "-a", text]
	process = subprocess.run(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
	output = process.stdout.decode("utf-8")
	err = process.stderr.decode("utf-8")
	if len(err) > 0:
		raise Exception(err)
	#end if

	data = json.loads(output)
	text = json.dumps(data, indent=4)
	print(text)

	# chown 1
	args = ["chown", "-R", vuser + ':' + vuser, tonDhtServerDir]
	subprocess.run(args)

	# start DHT-Server
	start_service(local, "dht-server")
#end define


def EnableJsonRpc(local):
	local.add_log("start EnableJsonRpc function", "debug")
	user = local.buffer.user

	jsonrpcinstaller_path = pkg_resources.resource_filename('mytoninstaller.scripts', 'jsonrpcinstaller.sh')
	local.add_log(f"Running script: {jsonrpcinstaller_path}", "debug")
	exit_code = run_as_root(["bash", jsonrpcinstaller_path, "-u", user])  # TODO: fix path
	if exit_code == 0:
		text = "EnableJsonRpc - {green}OK{endc}"
	else:
		text = "EnableJsonRpc - {red}Error{endc}"
	color_print(text)
#end define

def EnableTonHttpApi(local):
	local.add_log("start EnablePytonv3 function", "debug")
	user = local.buffer.user

	ton_http_api_installer_path = pkg_resources.resource_filename('mytoninstaller.scripts', 'tonhttpapiinstaller.sh')
	exit_code = run_as_root(["bash", ton_http_api_installer_path, "-u", user])
	if exit_code == 0:
		text = "EnableTonHttpApi - {green}OK{endc}"
	else:
		text = "EnableTonHttpApi - {red}Error{endc}"
	color_print(text)
#end define

def enable_ls_proxy(local):
	local.add_log("start enable_ls_proxy function", "debug")
	user = local.buffer.user
	ls_proxy_port = random.randint(2000, 65000)
	metrics_port = random.randint(2000, 65000)
	bin_name = "ls_proxy"
	ls_proxy_db_path = f"/var/{bin_name}"
	ls_proxy_path = f"{ls_proxy_db_path}/{bin_name}"
	ls_proxy_config_path = f"{ls_proxy_db_path}/ls-proxy-config.json"

	installer_path = pkg_resources.resource_filename('mytoninstaller.scripts', 'ls_proxy_installer.sh')
	local.add_log(f"Running script: {installer_path}", "debug")
	exit_code = run_as_root(["bash", installer_path, "-u", user])
	if exit_code != 0:
		color_print("enable_ls_proxy - {red}Error{endc}")
		raise Exception("enable_ls_proxy - Error")
	#end if

	# Прописать автозагрузку
	add2systemd(name=bin_name, user=user, start=ls_proxy_path, workdir=ls_proxy_db_path)

	# Первый запуск - создание конфига
	start_service(local, bin_name)
	stop_service(local, bin_name)

	# read ls_proxy config
	local.add_log("read ls_proxy config", "debug")
	ls_proxy_config = GetConfig(path=ls_proxy_config_path)

	# read mytoncore config
	local.add_log("read mytoncore config", "debug")
	mconfig = GetConfig(path=local.buffer.mconfig_path)
	ls_pubkey_path = mconfig.liteClient.liteServer.pubkeyPath
	ls_port = mconfig.liteClient.liteServer.port

	# read ls_pubkey
	with open(ls_pubkey_path, 'rb') as file:
		data = file.read()
		pubkey = data[4:]
		ls_pubkey = base64.b64encode(pubkey).decode("utf-8")
	#end with

	# prepare config
	ls_proxy_config.ListenAddr = f"0.0.0.0:{ls_proxy_port}"
	ls_proxy_config.MetricsAddr = f"127.0.0.1:{metrics_port}"
	ls_proxy_config.Backends = [{
		"Name": "local_ls",
		"Addr": f"127.0.0.1:{ls_port}",
		"Key": ls_pubkey
	}]

	# write ls_proxy config
	local.add_log("write ls_proxy config", "debug")
	SetConfig(path=ls_proxy_config_path, data=ls_proxy_config)

	# start ls_proxy
	start_service(local, bin_name)
	color_print("enable_ls_proxy - {green}OK{endc}")
#end define

def enable_ton_storage(local):
	local.add_log("start enable_ton_storage function", "debug")
	user = local.buffer.user
	udp_port = random.randint(2000, 65000)
	api_port = random.randint(2000, 65000)
	bin_name = "ton_storage"
	db_path = f"/var/{bin_name}"
	bin_path = f"{db_path}/{bin_name}"
	config_path = f"{db_path}/tonutils-storage-db/config.json"
	network_config = "/usr/bin/ton/global.config.json"

	installer_path = pkg_resources.resource_filename('mytoninstaller.scripts', 'ton_storage_installer.sh')
	local.add_log(f"Running script: {installer_path}", "debug")
	exit_code = run_as_root(["bash", installer_path, "-u", user])
	if exit_code != 0:
		color_print("enable_ton_storage - {red}Error{endc}")
		raise Exception("enable_ton_storage - Error")
	#end if

	# Прописать автозагрузку
	start_cmd = f"{bin_path} -network-config {network_config} -daemon -api 127.0.0.1:{api_port}"
	add2systemd(name=bin_name, user=user, start=start_cmd, workdir=db_path, force=True)

	# Первый запуск - создание конфига
	start_service(local, bin_name, sleep=10)
	stop_service(local, bin_name)

	# read ton_storage config
	local.add_log("read ton_storage config", "debug")
	ton_storage_config = GetConfig(path=config_path)

	# prepare config
	ton_storage_config.ListenAddr = f"0.0.0.0:{udp_port}"
	ton_storage_config.ExternalIP = get_own_ip()

	# write ton_storage config
	local.add_log("write ton_storage config", "debug")
	SetConfig(path=config_path, data=ton_storage_config)

	# backup config
	backup_config(local, config_path)

	# read mconfig
	local.add_log("read mconfig", "debug")
	mconfig_path = local.buffer.mconfig_path
	mconfig = GetConfig(path=mconfig_path)

	# edit mytoncore config file
	local.add_log("edit mytoncore config file", "debug")
	ton_storage = Dict()
	ton_storage.udp_port = udp_port
	ton_storage.api_port = api_port
	mconfig.ton_storage = ton_storage

	# write mconfig
	local.add_log("write mconfig", "debug")
	SetConfig(path=mconfig_path, data=mconfig)

	# start ton_storage
	start_service(local, bin_name)
	color_print("enable_ton_storage - {green}OK{endc}")
#end define

def enable_ton_storage_provider(local):
	local.add_log("start enable_ton_storage_provider function", "debug")
	user = local.buffer.user
	udp_port = random.randint(2000, 65000)
	bin_name = "ton_storage_provider"
	db_path = f"/var/{bin_name}"
	bin_path = f"{db_path}/{bin_name}"
	config_path = f"{db_path}/config.json"
	network_config = "/usr/bin/ton/global.config.json"

	installer_path = pkg_resources.resource_filename('mytoninstaller.scripts', 'ton_storage_provider_installer.sh')
	local.add_log(f"Running script: {installer_path}", "debug")
	exit_code = run_as_root(["bash", installer_path, "-u", user])
	if exit_code != 0:
		color_print("enable_ton_storage_provider - {red}Error{endc}")
		raise Exception("enable_ton_storage_provider - Error")
	#end if

	# Прописать автозагрузку
	start_cmd = f"{bin_path} -network-config {network_config}"
	add2systemd(name=bin_name, user=user, start=start_cmd, workdir=db_path, force=True)

	# Первый запуск - создание конфига
	start_service(local, bin_name, sleep=10)
	stop_service(local, bin_name)

	# read mconfig
	local.add_log("read mconfig", "debug")
	mconfig_path = local.buffer.mconfig_path
	mconfig = GetConfig(path=mconfig_path)

	# read ton_storage_provider config
	local.add_log("read ton_storage_provider config", "debug")
	config = GetConfig(path=config_path)

	# prepare config
	config.ListenAddr = f"0.0.0.0:{udp_port}"
	config.ExternalIP = get_own_ip()
	config.Storages[0].BaseURL = f"http://127.0.0.1:{mconfig.ton_storage.api_port}"

	# write ton_storage_provider config
	local.add_log("write ton_storage_provider config", "debug")
	SetConfig(path=config_path, data=config)

	# backup config
	backup_config(local, config_path)

	# get provider pubkey
	key_bytes = base64.b64decode(config.ProviderKey)
	pubkey_bytes = key_bytes[32:64]

	# edit mytoncore config file
	local.add_log("edit mytoncore config file", "debug")
	provider = Dict()
	provider.udp_port = udp_port
	provider.config_path = config_path
	provider.pubkey = pubkey_bytes.hex()
	mconfig.ton_storage.provider = provider

	# write mconfig
	local.add_log("write mconfig", "debug")
	SetConfig(path=mconfig_path, data=mconfig)

	# Подтянуть событие в mytoncore.py
	cmd = 'python3 -m mytoncore -e "enable_ton_storage_provider"'
	args = ["su", "-l", user, "-c", cmd]
	subprocess.run(args)

	# start ton_storage_provider
	start_service(local, bin_name)
	color_print("enable_ton_storage_provider - {green}OK{endc}")
#end define

def DangerousRecoveryValidatorConfigFile(local):
	local.add_log("start DangerousRecoveryValidatorConfigFile function", "info")

	# Get keys from keyring
	keys = list()
	keyringDir = "/var/ton-work/db/keyring/"
	keyring = os.listdir(keyringDir)
	os.chdir(keyringDir)
	sorted(keyring, key=os.path.getmtime)
	for item in keyring:
		b64String = hex2b64(item)
		keys.append(b64String)
	#end for

	# Create config object
	vconfig = Dict()
	vconfig["@type"] = "engine.validator.config"
	vconfig.out_port = 3278

	# Create addrs object
	buff = Dict()
	buff["@type"] = "engine.addr"
	buff.ip = ip2int(get_own_ip())
	buff.port = None
	buff.categories = [0, 1, 2, 3]
	buff.priority_categories = []
	vconfig.addrs = [buff]

	# Get liteserver fragment
	mconfig_path = local.buffer.mconfig_path
	mconfig = GetConfig(path=mconfig_path)
	lkey = mconfig.liteClient.liteServer.pubkeyPath
	lport = mconfig.liteClient.liteServer.port

	# Read lite server pubkey
	file = open(lkey, 'rb')
	data = file.read()
	file.close()
	ls_pubkey = data[4:]

	# Search lite server priv key
	for item in keyring:
		path = keyringDir + item
		file = open(path, 'rb')
		data = file.read()
		file.close()
		privkey = data[4:]
		pubkey = get_ed25519_pubkey(privkey)
		if pubkey == ls_pubkey:
			ls_id = hex2b64(item)
			keys.remove(ls_id)
	#end for

	# Create LS object
	buff = Dict()
	buff["@type"] = "engine.liteServer"
	buff.id = ls_id
	buff.port = lport
	vconfig.liteservers = [buff]

	# Get validator-console fragment
	ckey = mconfig.validatorConsole.pubKeyPath
	addr = mconfig.validatorConsole.addr
	buff = addr.split(':')
	cport = int(buff[1])

	# Read validator-console pubkey
	file = open(ckey, 'rb')
	data = file.read()
	file.close()
	vPubkey = data[4:]

	# Search validator-console priv key
	for item in keyring:
		path = keyringDir + item
		file = open(path, 'rb')
		data = file.read()
		file.close()
		privkey = data[4:]
		pubkey = get_ed25519_pubkey(privkey)
		if pubkey == vPubkey:
			vcId = hex2b64(item)
			keys.remove(vcId)
	#end for

	# Create VC object
	buff = Dict()
	buff2 = Dict()
	buff["@type"] = "engine.controlInterface"
	buff.id = vcId
	buff.port = cport
	buff2["@type"] = "engine.controlProcess"
	buff2.id = None
	buff2.permissions = 15
	buff.allowed = buff2
	vconfig.control = [buff]

	# Get dht fragment
	files = os.listdir("/var/ton-work/db")
	for item in files:
		if item[:3] == "dht":
			dhtS = item[4:]
			dhtS = dhtS.replace('_', '/')
			dhtS = dhtS.replace('-', '+')
			break
	#end for

	# Get ght from keys
	for item in keys:
		if dhtS in item:
			dhtId = item
			keys.remove(dhtId)
	#end for

	# Create dht object
	buff = Dict()
	buff["@type"] = "engine.dht"
	buff.id = dhtId
	vconfig.dht = [buff]

	# Create adnl object
	adnl2 = Dict()
	adnl2["@type"] = "engine.adnl"
	adnl2.id = dhtId
	adnl2.category = 0

	# Create adnl object
	adnlId = hex2b64(mconfig["adnlAddr"])
	keys.remove(adnlId)
	adnl3 = Dict()
	adnl3["@type"] = "engine.adnl"
	adnl3.id = adnlId
	adnl3.category = 0

	# Create adnl object
	adnl1 = Dict()
	adnl1["@type"] = "engine.adnl"
	adnl1.id = keys.pop(0)
	adnl1.category = 1

	vconfig.adnl = [adnl1, adnl2, adnl3]

	# Get dumps from tmp
	dumps = list()
	dumpsDir = "/tmp/mytoncore/"
	dumpsList = os.listdir(dumpsDir)
	os.chdir(dumpsDir)
	sorted(dumpsList, key=os.path.getmtime)
	for item in dumpsList:
		if "ElectionEntry.json" in item:
			dumps.append(item)
	#end for

	# Create validators object
	validators = list()

	# Read dump file
	while len(keys) > 0:
		dumpPath = dumps.pop()
		file = open(dumpPath, 'rt')
		data = file.read()
		file.close()
		dump = json.loads(data)
		vkey = hex2b64(dump["validatorKey"])
		temp_key = Dict()
		temp_key["@type"] = "engine.validatorTempKey"
		temp_key.key = vkey
		temp_key.expire_at = dump["endWorkTime"]
		adnl_addr = Dict()
		adnl_addr["@type"] = "engine.validatorAdnlAddress"
		adnl_addr.id = adnlId
		adnl_addr.expire_at = dump["endWorkTime"]

		# Create validator object
		validator = Dict()
		validator["@type"] = "engine.validator"
		validator.id = vkey
		validator.temp_keys = [temp_key]
		validator.adnl_addrs = [adnl_addr]
		validator.election_date = dump["startWorkTime"]
		validator.expire_at = dump["endWorkTime"]
		if vkey in keys:
			validators.append(validator)
			keys.remove(vkey)
		#end if
	#end while

	# Add validators object to vconfig
	vconfig.validators = validators


	print("vconfig:", json.dumps(vconfig, indent=4))
	print("keys:", keys)
#end define


def CreateSymlinks(local):
	local.add_log("start CreateSymlinks fuction", "debug")
	cport = local.buffer.cport

	mytonctrl_file = "/usr/bin/mytonctrl"
	fift_file = "/usr/bin/fift"
	liteclient_file = "/usr/bin/lite-client"
	validator_console_file = "/usr/bin/validator-console"
	env_file = "/etc/environment"
	file = open(mytonctrl_file, 'wt')
	# file.write("/usr/bin/python3 /usr/src/mytonctrl/mytonctrl.py $@")  # TODO: fix path
	file.write("/usr/bin/python3 -m mytonctrl $@")  # TODO: fix path
	file.close()
	file = open(fift_file, 'wt')
	file.write("/usr/bin/ton/crypto/fift $@")
	file.close()
	file = open(liteclient_file, 'wt')
	file.write("/usr/bin/ton/lite-client/lite-client -C /usr/bin/ton/global.config.json $@")
	file.close()
	if cport:
		file = open(validator_console_file, 'wt')
		file.write("/usr/bin/ton/validator-engine-console/validator-engine-console -k /var/ton-work/keys/client -p /var/ton-work/keys/server.pub -a 127.0.0.1:" + str(cport) + " $@")
		file.close()
		args = ["chmod", "+x", validator_console_file]
		subprocess.run(args)
	args = ["chmod", "+x", mytonctrl_file, fift_file, liteclient_file]
	subprocess.run(args)

	# env
	fiftpath = "export FIFTPATH=/usr/src/ton/crypto/fift/lib/:/usr/src/ton/crypto/smartcont/"
	file = open(env_file, 'rt+')
	text = file.read()
	if fiftpath not in text:
		file.write(fiftpath + '\n')
	file.close()
#end define


def EnableMode(local):
	args = ["python3", "-m", "mytoncore", "-e"]
	if local.buffer.mode:
		args.append("enable_mode_" + local.buffer.mode)
	else:
		return
	args = ["su", "-l", local.buffer.user, "-c", ' '.join(args)]
	subprocess.run(args)
