import asyncio
import ujson as json
from pathlib import Path
from loguru import logger
from httpx import AsyncClient
import hashlib
import os
import shutil
import subprocess
import tempfile

# CDN
CDN_URL = "http://jia.liufly.top:20001/music/"
USE_CDN = True
VERSION = "0.2.0"

# 手动维护的插件：不参与下载，生成 plugins.json 时直接追加
# 对应的 JS 文件需自行放置到 dist 目录，文件名取自 URL
MANUAL_PLUGINS = [
    {
        "name": "酷我",
        "url": f"{CDN_URL}2d67f88f71ca165c29cb8994efe3c262.js",
        "version": "0.1.0",
    },
]

# 定义路径常量
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
DATA_JSON_PATH = DATA_DIR / "origins.json"

DIST_DIR = Path(__file__).parent.parent / "dist"
DIST_DIR.mkdir(exist_ok=True)
DIST_JSON_PATH = DIST_DIR / "plugins.json"

# 重试相关常量
MAX_RETRIES = 3
RETRY_DELAY = 1
REQUEST_TIMEOUT = 10.0

# 校验相关常量
MIN_PLUGIN_SIZE = 200  # 小于此大小的插件内容视为无效
SMOKE_MAX_CONCURRENCY = int(os.environ.get("MUSICFREE_SMOKE_CONCURRENCY", "5"))  # 冒烟测试并发上限
SMOKE_TEST = os.environ.get("MUSICFREE_SMOKE", "0") in ("1", "true", "yes")  # 功能冒烟测试开关（默认关闭）
SMOKE_TIMEOUT = int(os.environ.get("MUSICFREE_SMOKE_TIMEOUT", "5000"))
SMOKE_QUERY = os.environ.get("MUSICFREE_SMOKE_QUERY", "周杰伦")
SMOKE_RUNNER = Path(__file__).parent / "smoke_check.cjs"
        

def _looks_like_html(content: str) -> bool:
    head = content.lstrip().lower()[:500]
    return "<html" in head or "<!doctype" in head or "<head" in head or "<body" in head or "loading..." in head


def validate_plugin_content(content: str) -> tuple[bool, str]:
    """静态校验插件内容：拒绝空/过小/HTML/语法错误的插件。

    Args:
        content: 插件 JS 内容

    Returns:
        (是否可用, 原因)
    """
    if not content or not content.strip():
        return False, "内容为空"
    if len(content) < MIN_PLUGIN_SIZE:
        return False, f"内容过小（{len(content)} 字节）"
    if _looks_like_html(content):
        return False, "内容为 HTML 而非 JS"
    node = shutil.which("node")
    if node:
        try:
            res = subprocess.run(
                [node, "--check", "-"],
                input=content,
                capture_output=True,
                text=True,
                timeout=15,
            )
            if res.returncode != 0:
                err = (res.stderr or res.stdout or "unknown error").strip().splitlines()
                return False, f"JS 语法错误: {err[0][:120]}"
        except Exception as e:
            logger.warning(f"语法检查不可用（{e}），保留该插件")
    else:
        logger.warning("未找到 node，跳过 JS 语法校验")
    return True, "ok"


def run_smoke_check(content: str, name: str) -> tuple[bool, str]:
    """可选的功能冒烟测试：用 node 加载插件并真实调用 search(query)，5s 超时。

    Args:
        content: 插件 JS 内容
        name: 插件名称（仅用于日志）

    Returns:
        (是否通过, 说明)
    """
    node = shutil.which("node")
    if not node or not SMOKE_RUNNER.exists():
        logger.warning(f"跳过功能冒烟测试: 未找到 node 或 smoke_check.cjs")
        return True, "跳过: 未找到 node/smoke_check.cjs"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".js", delete=False, mode="w", encoding="utf-8") as f:
            f.write(content)
            tmp_path = f.name
        res = subprocess.run(
            [node, str(SMOKE_RUNNER), tmp_path, SMOKE_QUERY, str(SMOKE_TIMEOUT), "music"],
            capture_output=True,
            text=True,
            timeout=SMOKE_TIMEOUT / 1000 + 10,
        )
        out = (res.stdout or "").strip()
        # 优先取最后一行（插件内部可能打印调试信息）
        line = out.splitlines()[-1] if out else ""
        try:
            info = json.loads(line)
        except Exception:
            return False, f"冒烟输出异常: {line[:120]}"
        # 通过标准："ok":true 且有 items 数 > 0
        if info.get("ok"):
            return True, info.get("info", "ok")
        return False, info.get("info", "未知失败")
    except subprocess.TimeoutExpired:
        return False, f"超过 {SMOKE_TIMEOUT / 1000}s 超时"
    except Exception as e:
        logger.warning(f"功能冒烟测试出错（{e}），保留该插件")
        return True, f"跳过: {e}"
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


async def fetch_sub_plugins(url: str, client: AsyncClient) -> list:

    """从订阅源获取单个插件列表

    Args:
        url: 订阅源URL
        client: HTTP客户端实例

    Returns:
        插件列表,获取失败返回空列表
    """
    for retry in range(MAX_RETRIES):
        try:
            response = await client.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            # 兼容两种订阅格式：{"plugins": [...]} 或直接是 [...] 数组
            if isinstance(data, list):
                return data
            return data.get("plugins", [])
        except Exception as e:
            if retry == MAX_RETRIES - 1:
                logger.error(
                    f"订阅源 {url} 获取失败(重试{retry + 1}/{MAX_RETRIES}): {str(e)}"
                )
                return []
            logger.warning(
                f"订阅源 {url} 获取失败(重试{retry + 1}/{MAX_RETRIES}): {str(e)}"
            )
            await asyncio.sleep(RETRY_DELAY)


async def fetch_plugins(plugins: list, client: AsyncClient) -> list:
    """获取有效的插件列表

    Args:
        plugins: 待处理的插件列表
        client: HTTP客户端实例

    Returns:
        有效的插件列表
    """
    smoke_semaphore = asyncio.Semaphore(SMOKE_MAX_CONCURRENCY)
    seen_urls = set()  # 用于去重
    name_count = {}  # 用于统计重名插件

    async def download_and_process_plugin(plugin: dict) -> tuple[bool, dict]:
        """下载插件并处理URL

        Args:
            plugin: 单个插件信息

        Returns:
            (成功标志, 处理后的插件信息)
        """
        url = plugin["url"]
        if url in seen_urls:
            return False, plugin
        seen_urls.add(url)

        for retry in range(MAX_RETRIES):
            try:
                response = await client.get(url, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()

                # 计算 MD5
                md5 = hashlib.md5(url.encode("utf-8")).hexdigest()
                
                # 处理 JS 文件内容，替换原始 URL 为 CDN URL
                content = response.text
                if USE_CDN:
                    # 替换整个原始 GitHub URL 为 CDN URL
                    original_url = url.replace('\\', '')  # 处理可能存在的转义字符
                    cdn_url = f"{CDN_URL}{md5}.js"
                    content = content.replace(original_url, cdn_url)

                # 静态校验插件内容（拒绝 HTML/语法错误/过小的插件）
                is_valid, reason = validate_plugin_content(content)
                if not is_valid:
                    logger.warning(f"插件 {plugin.get('name', url)} 校验失败，跳过: {reason}")
                    return False, plugin

                # 功能冒烟测试（默认关闭，MUSICFREE_SMOKE=1 开启）
                if SMOKE_TEST:
                    async with smoke_semaphore:
                        ok, smoke_info = await asyncio.to_thread(run_smoke_check, content, plugin.get("name", url))
                    if not ok:
                        logger.warning(f"插件 {plugin.get('name', url)} 冒烟失败，跳过: {smoke_info}")
                        return False, plugin

                # 保存处理后的插件文件
                output_path = DIST_DIR / f"{md5}.js"
                output_path.write_text(content, encoding='utf-8')

                # 处理插件信息
                new_plugin = plugin.copy()
                name = plugin.get("name", url)
                
                # 替换敏感词
                name = name.replace("网易云", "W").replace("QQ", "T")

                # 处理重名
                if name in name_count:
                    name_count[name] += 1
                    new_plugin["name"] = f"{name}_{name_count[name]}"
                else:
                    name_count[name] = 0
                    new_plugin["name"] = name

                # 使用 CDN 替换原始 URL
                if USE_CDN:
                    new_plugin["url"] = f"{CDN_URL}{md5}.js"

                logger.success(f"插件 {new_plugin['name']} 下载成功")
                return True, new_plugin

            except Exception as e:
                if retry == MAX_RETRIES - 1:
                    logger.error(
                        f"插件 {plugin.get('name', url)} 下载失败(重试{retry + 1}/{MAX_RETRIES}): {str(e)}"
                    )
                    return False, plugin
                logger.warning(
                    f"插件 {plugin.get('name', url)} 下载失败(重试{retry + 1}/{MAX_RETRIES}): {str(e)}"
                )
                await asyncio.sleep(RETRY_DELAY)

    # 并发下载和处理插件
    tasks = [download_and_process_plugin(plugin) for plugin in plugins]
    results = await asyncio.gather(*tasks)

    return [new_plugin for success, new_plugin in results if success]


async def load_origins() -> dict:
    """加载源配置文件

    Returns:
        源配置字典,加载失败返回空配置
    """
    try:
        with open(DATA_JSON_PATH, encoding="utf8") as f:
            return json.loads(f.read())
    except Exception as e:
        logger.error(f"读取源列表文件失败: {str(e)}")
        return {"sources": [], "singles": []}


async def save_results(results: dict) -> bool:
    """保存结果到文件

    Args:
        results: 要保存的结果数据

    Returns:
        保存是否成功
    """
    try:
        with open(DIST_JSON_PATH, "w", encoding="utf-8") as file:
            json_str = json.dumps(results, ensure_ascii=False, indent=2)
            json_str = json_str.replace("\\/", "/")
            file.write(json_str)
        logger.success(f"插件列表已保存至: {DIST_JSON_PATH}")
        return True
    except Exception as e:
        logger.error(f"保存结果文件失败: {str(e)}")
        return False


async def collect_plugins(origins: dict, client: AsyncClient) -> list:
    """收集所有插件

    Args:
        origins: 源配置信息
        client: HTTP客户端实例

    Returns:
        收集到的所有插件列表
    """
    all_plugins = []

    # 获取订阅源插件
    if sources := origins.get("sources", []):
        logger.info(f"正在获取 {len(sources)} 个订阅源的插件...")
        for source_url in sources:
            plugins = await fetch_sub_plugins(source_url, client)
            if plugins:
                logger.info(f"从 {source_url} 获取到 {len(plugins)} 个插件")
                all_plugins.extend(plugins)

    # 添加单独插件
    if singles := origins.get("singles", []):
        logger.info(f"添加 {len(singles)} 个单独插件...")
        all_plugins.extend(singles)

    return all_plugins


async def main():
    """主函数"""
    logger.info("开始执行插件更新任务...")

    # 清空 dist 目录中的 JS 文件（保留手动插件对应的文件）
    manual_files = {p["url"].rsplit("/", 1)[-1] for p in MANUAL_PLUGINS}
    for js_file in DIST_DIR.glob("*.js"):
        if js_file.name in manual_files:
            logger.info(f"保留手动插件文件: {js_file.name}")
            continue
        js_file.unlink()
    logger.info("已清空 dist 目录中的 JS 文件（手动插件除外）")

    # 1. 加载配置
    origins = await load_origins()
    if not origins:
        return

    # 2. 处理插件
    async with AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
        # 收集所有插件
        all_plugins = await collect_plugins(origins, client)
        if not all_plugins:
            logger.warning("未获取到任何插件")
            return

        # 下载和验证插件
        logger.info(f"开始下载和验证 {len(all_plugins)} 个插件...")
        valid_plugins = await fetch_plugins(all_plugins, client)

        # 追加手动维护的插件（按 URL 去重，不参与下载）
        existing_urls = {p["url"] for p in valid_plugins}
        manual_added = 0
        for plugin in MANUAL_PLUGINS:
            if plugin["url"] in existing_urls:
                continue
            valid_plugins.append(plugin)
            existing_urls.add(plugin["url"])
            manual_added += 1
        if manual_added:
            logger.info(f"已追加 {manual_added} 个手动插件")

        if not valid_plugins:
            logger.error("没有有效的插件")
            return

        logger.info(f"成功验证 {len(valid_plugins)} 个插件")

    # 3. 保存结果
    if await save_results({"desc": VERSION, "plugins": valid_plugins}):
        logger.success(f"任务完成! 共更新 {len(valid_plugins)} 个插件")


if __name__ == "__main__":
    asyncio.run(main())
