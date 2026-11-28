// 自动生成，不要手改：python3 scripts/build_runtime_bundles.py（CI 用 --check 校验与来源一致）。
// 妙搭部署过滤 .py，且服务端只部署 main.js 沿 import 能追踪到的文件，所以运行时文件以常量形式随模块部署。
// 美股已登记可取数 ticker 清单（与 core/market_data/market.py 的 US_EXCHANGE_MAP 同源；BFF 预校验未登记美股）。
export const US_EXCHANGE_MAP_SOURCE_SHA256 = '7adcda005a4b9bc24aa720451ad78386d7cca8492ac613c63aacc550e78d503f';
export const US_REGISTERED_TICKERS = ["AAPL", "NVDA", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "AVGO", "COST", "NFLX", "AMD", "INTC", "QCOM", "ADBE", "PEP", "CSCO", "KO", "JPM", "BRK.B", "V", "WMT", "JNJ", "MA", "PG", "XOM", "UNH", "HD", "DIS", "BAC", "ORCL"];
