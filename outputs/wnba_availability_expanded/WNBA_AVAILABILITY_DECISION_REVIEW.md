# WNBA Player-Availability Challenger: 2026-05-14 to 2026-07-20

## Decision

**Keep as a shadow challenger: probability quality improved, winner accuracy did not.** The paired Brier interval is below zero, but top-pick accuracy is statistically unresolved and slightly worse at the 1.0× prespecified scale. Retrospective inputs still block promotion.

## What was actually tested

- Incumbent: fixed `wnba-elo-trend-lr-v3` coefficients recomputed walk-forward for the historical date; a frozen ledger probability is used when one exists.
- Addition: official WNBA status × pregame projected minutes × a heavily shrunk 10-game box plus/minus proxy above replacement.
- Status sources: official WNBA PDFs plus timestamp-filtered ESPN event injuries. ESPN fills official omissions; contradictory explicit statuses fail closed in production.
- Expanded-source rule: ESPN secondary data was enabled for 1 specifically requested matchup(s); all other games use the official report only to avoid survivor-biased retrospective ESPN injury lists.
- Conflict sensitivity: the diagnostic table also shows a clearly labeled most-conservative resolution (the lower active probability). It is not production-authorized.
- Probability bridge: availability points are added in probit-margin space using the pre-window empirical WNBA margin standard deviation.
- Window: 169 reconstructed matchups, including 164 settled games. Unsettled games are excluded from accuracy and Brier.
- Provenance warning: the official PDFs carry historical publication timestamps but were downloaded retrospectively. This is diagnostic, not a locked point-in-time promotion test.

## Data validation

- Official PDFs parsed: **208**.
- Official player-status rows parsed, including repeated report updates: **4422**.
- Settled games with a complete submitted report and mapped priors: **142 / 164**.
- Settled games evaluable only after conservative conflict resolution: **142 / 164**.
- Empirical pre-window home-margin sigma: **14.778 points**.
- Parser/status counts: `{"Available": 313, "Doubtful": 40, "Out": 3487, "Probable": 191, "Questionable": 391}`.
- Scoreboard events considered: **180**; v3 candidates produced: **169**; v3 skips: **11**.

### Source reports

| Report time (UTC) | Rows | Submitted teams | Not submitted | SHA-256 |
|---|---:|---:|---:|---|
| 2026-05-14T23:00:00+00:00 | 15 | 6 | 4 | `be7e07a498a15b277216fb2283236e1435b0501097b9c4d6bab0656a062b7d63` |
| 2026-05-14T23:30:00+00:00 | 15 | 6 | 4 | `44bad205ec3733caa790f829471c761fec75a35705910dafebc95e210acea0fb` |
| 2026-05-15T01:00:00+00:00 | 23 | 10 | 0 | `c1299b3f7a8313f433bd7e6562502f61c787df8ba1ba670066ce42a8ca3c0436` |
| 2026-05-15T01:30:00+00:00 | 23 | 10 | 0 | `cd483d1a824137bc3d1b1e86e582853f3b07ff2412992db1adb0f9b7b560bee2` |
| 2026-05-15T22:30:00+00:00 | 18 | 8 | 0 | `3f325edef1474415d8d0fa5c8eceacac6b882eee9596d0a835b79e69c6139bea` |
| 2026-05-15T23:00:00+00:00 | 18 | 8 | 0 | `b4e40c26ddfc134a04fb34969afffe803877519a79fca5f39573369db3b967ab` |
| 2026-05-16T01:00:00+00:00 | 20 | 8 | 0 | `154cb8ad2e6621bf216b9ef282ebfb5ce10746424295b9297a382a4b40713242` |
| 2026-05-16T01:30:00+00:00 | 23 | 8 | 0 | `9b6b53f6f7ee28fffc85c5e2897967a9fbf8c50db58385016236b0fb365868db` |
| 2026-05-17T16:30:00+00:00 | 21 | 8 | 4 | `01cec6d6acb6a3973396ca7fd5eb837bb50041246de392e28fcccca53b2f41cb` |
| 2026-05-17T17:00:00+00:00 | 21 | 8 | 4 | `3150dd493fc0a02171275b3ed6f5d69ac27051da7dfda997aceeb3271f320fc7` |
| 2026-05-17T21:00:00+00:00 | 22 | 8 | 4 | `0e02506a181cb77b0e4eae50d68a2f1a4546069a8d6971da9461583b856f4329` |
| 2026-05-17T21:30:00+00:00 | 24 | 8 | 4 | `70fb6ad658c361c37354f7b29a30c044f28034a8922eacae4d350707c60f7c19` |
| 2026-05-17T22:00:00+00:00 | 27 | 10 | 2 | `76879f70d4924cf279add2ca767825e7449deef56b108c41b3ff96e9e46156ef` |
| 2026-05-17T22:30:00+00:00 | 29 | 10 | 2 | `f47b41ac1c2de1d9a7764841d6d4d0ab1cbfb1d8df59f077665fa8f73ad88055` |
| 2026-05-18T23:00:00+00:00 | 12 | 5 | 1 | `d0049c5523b8cb0e6fe145d94e8fee9d90c96f87190135990ba5b2c4c2fbb35a` |
| 2026-05-18T23:30:00+00:00 | 12 | 5 | 1 | `2f5e892a6367719e778cdc15dd297c474a53fe4e8dd111d1f96f4c2fcdc0a097` |
| 2026-05-19T01:00:00+00:00 | 15 | 6 | 0 | `ea05b1c808f679b4f996254830e696f362bd51a701e4782d17dc0f4768a3949a` |
| 2026-05-19T01:30:00+00:00 | 15 | 6 | 0 | `40931b09e44eeaf4e0a567a4c1de352a04b1b1b97cf7b3a463167ce680d97f50` |
| 2026-05-20T01:00:00+00:00 | 18 | 8 | 0 | `d80cd865b57d206e842591ff18a31741c523ad914305992e9eb49a7b726c958a` |
| 2026-05-20T01:30:00+00:00 | 18 | 8 | 0 | `db84df2473288c67ca25f9329fb9a1c1d51eae95eeb33ea40a9b45305e643cd0` |
| 2026-05-20T22:00:00+00:00 | 27 | 10 | 2 | `d1169c51a803d9456d1c3aa28b9227a207c467eaab4b536e889dcbdbaccf355b` |
| 2026-05-20T22:30:00+00:00 | 30 | 10 | 2 | `a46a1434bbe2ef2032e2cb7d5f7f89572303a7f460c3d3dd0777cc3866048330` |
| 2026-05-21T00:00:00+00:00 | 35 | 12 | 0 | `b036e2fdf152c696c10ac526337f93298789ca6c29d3d5c33912a4ec95539573` |
| 2026-05-21T00:30:00+00:00 | 35 | 12 | 0 | `7838c23f2d8a5b2c5983c619359153bbccbb96f9679249ab264d4d118a404504` |
| 2026-05-21T01:00:00+00:00 | 36 | 12 | 0 | `3f5ea4a2d252216a04a5a15a8709ad9bb18b72b7fa41b129c9341fd0b1c74015` |
| 2026-05-21T01:30:00+00:00 | 36 | 12 | 0 | `b8ada49866dc156b71392f9343f738e865af8fbc45b28e6ca379e0c288a48103` |
| 2026-05-21T23:30:00+00:00 | 19 | 8 | 2 | `9b2328c35508c84f0cee8ac5c74771a3676a1120b384dac491e30a2e5290655c` |
| 2026-05-22T01:00:00+00:00 | 25 | 9 | 1 | `967458f34cc155c2de01b5795da13a7bfa6dc20474eec7a4090e5f3fbb4b6e27` |
| 2026-05-22T01:30:00+00:00 | 26 | 9 | 1 | `d4559ed1db30680acdce4a2f7e56f87bed2c1f206bc0baa2b747646123883fcf` |
| 2026-05-22T22:30:00+00:00 | 32 | 11 | 1 | `b37321ace22118784740befc3277d30e2a3d84988814e44162b725e6e41fdb49` |
| 2026-05-22T23:00:00+00:00 | 32 | 11 | 1 | `5464724705e3ea819ad00ab43af36a5ad69d2360eac51d95f7d6b1b3384f3619` |
| 2026-05-23T01:00:00+00:00 | 35 | 12 | 0 | `1cbe2377e4d8d01b77282e2b250a4082150e8ff692ea51585f7962f37c17b471` |
| 2026-05-23T01:30:00+00:00 | 35 | 12 | 0 | `5a28feea6d77339ac3b72d996e9af7beb7dfd74a707bdb536fca1cb2e5e9276b` |
| 2026-05-23T16:00:00+00:00 | 21 | 6 | 6 | `931e0e4850c65116806a832a03100bc77159faba2f4104d7eb04390db4660892` |
| 2026-05-23T16:30:00+00:00 | 21 | 6 | 6 | `09b5cf4d9098ecfa0e5e207e2270dbe1ed32b26deffd68f4126993d6e62cceda` |
| 2026-05-23T21:00:00+00:00 | 26 | 10 | 2 | `5bb5eec08f993239c8f99803088054cadf01d84777c1e35fd9e2aa0f59f59e57` |
| 2026-05-23T21:30:00+00:00 | 27 | 10 | 2 | `c27518012a9ed9df0aa23195cf0b35ebbe83cf938dd39298530a88accdec604d` |
| 2026-05-23T23:00:00+00:00 | 27 | 10 | 2 | `249fadfb6836a9f6c16a5fb84b69d29cf08532a07f1d5290b2b7b06a91e56064` |
| 2026-05-23T23:30:00+00:00 | 28 | 10 | 2 | `ba995b51e6d0417bd410c064d452b730686e730d8fedd1ecf58179bfdf2571d1` |
| 2026-05-24T18:00:00+00:00 | 10 | 6 | 3 | `5978038e44dbe28a1a80cda2d2f9e08a8866a426246e2797e1f2bd17f4442e35` |
| 2026-05-24T18:30:00+00:00 | 12 | 6 | 3 | `b04272575c454bab27707596e1778a7393a96153198c420d5c99dc477d5e8905` |
| 2026-05-24T19:00:00+00:00 | 13 | 5 | 4 | `8b7b9f4c17cd9098e5ce4b917c5d769a0c0ef263b57ed2b818eb8e42fd37b177` |
| 2026-05-24T21:30:00+00:00 | 14 | 6 | 3 | `eb528ab96feb71811b6bdd4e520ba425ce9d478642dd145172c9a0a4b2017739` |
| 2026-05-25T23:00:00+00:00 | 8 | 4 | 0 | `ceaed7e016c03c0bc1e454713f4b167b2a2c73ee967a28a0c555896146f585bb` |
| 2026-05-25T23:30:00+00:00 | 9 | 4 | 0 | `5ba989c986925ef7038dbfcc54f9141bd863363b468575b08834ffebe72a842d` |
| 2026-05-26T01:00:00+00:00 | 9 | 4 | 0 | `f11a9cd4c107d371434d22a19b68deae68582592521f44a98526dfa0b54031f1` |
| 2026-05-26T01:30:00+00:00 | 11 | 4 | 0 | `5dd13e4ed2ee71142a8f95785c48d08467c3f4bf3eb880ebcce7f88e87a05c7e` |
| 2026-05-27T22:00:00+00:00 | 27 | 12 | 2 | `d3c094ba6a7dff5bddc725d02a3ed688c62b03162bd2ea7f291dee7785831156` |
| 2026-05-27T22:30:00+00:00 | 27 | 12 | 2 | `e16919d7cbcaf7ab624624c51ea3c69f3ebdc2f0f92c7182d2d7eda9a571dd2f` |
| 2026-05-27T23:00:00+00:00 | 28 | 12 | 2 | `aca84309bd905c7647691b4132ba56c8acaa9acd3f131404f5d82b161e67f50a` |
| 2026-05-27T23:30:00+00:00 | 28 | 12 | 2 | `a8277866ede0fc2c5677de9304c0e843917c803e706413854e55885eb97c60fa` |
| 2026-05-28T00:00:00+00:00 | 33 | 14 | 0 | `fc6f448af80215fd1f5dd8aff9c6aab126552d640ad07803d8c2592ff96c6734` |
| 2026-05-28T00:30:00+00:00 | 33 | 14 | 0 | `e4638f62678ed1457a049ae52b0db562d1bb7103d0713c2ef46357b285d7ee44` |
| 2026-05-28T01:00:00+00:00 | 33 | 14 | 0 | `df3f49a6be51ece174184211c10d56215298f4f905f90ccdc99c9d20115c7735` |
| 2026-05-28T01:30:00+00:00 | 34 | 14 | 0 | `83592f04347230aaa374959864109b835c1966dbea774577557ba48b515055bd` |
| 2026-05-28T23:00:00+00:00 | 19 | 10 | 2 | `298e97e8c03e1a93528907f8ae46af3021c2ff6f8274702de6d376128b07c80d` |
| 2026-05-28T23:30:00+00:00 | 22 | 11 | 1 | `004eb70084723fa60cc4c744f749a8be6fe15b82733153c75412becde6dd7a70` |
| 2026-05-29T01:00:00+00:00 | 23 | 12 | 0 | `75b406091116637c61d748b4f723972aa325103273a1422ce969202f83bf7e6a` |
| 2026-05-29T01:30:00+00:00 | 25 | 12 | 0 | `5bf8467f32cecbf6a1e4c2c97f821a5aa55e36eee42e5097e5178e969751d01f` |
| 2026-05-29T22:30:00+00:00 | 32 | 9 | 3 | `8bb09b9d6955f4b4b703317734d6d3cd21074974181ff09bb576f745db68722b` |
| 2026-05-29T23:00:00+00:00 | 35 | 9 | 3 | `e111a748b68be79f03e1d790e39e4a8813478a6381c2e51b7b9e4998432674ed` |
| 2026-05-30T16:00:00+00:00 | 10 | 4 | 4 | `7df69829096cb436d1c9075c9abb2a601704684bce6f2d486e6062d20b572221` |
| 2026-05-30T16:30:00+00:00 | 10 | 4 | 4 | `1ecc80b83ba091770085dce3bc39923a221649aca7bad683447f31f927bf0bf5` |
| 2026-05-30T21:00:00+00:00 | 15 | 6 | 1 | `4206af559268704b327eceb6d8d968dc55b94bd5f9ad437edfcc48525eca2e5b` |
| 2026-05-30T21:30:00+00:00 | 16 | 6 | 2 | `538e3141e2a7888b00fa40a547eafe7c4ef25275a466deaf5c19bc82cdce9baf` |
| 2026-05-30T23:00:00+00:00 | 16 | 6 | 2 | `608c998da6df2dee37d9ed7c38750f626799de2a919653d002533497c87fbc75` |
| 2026-05-31T19:00:00+00:00 | 4 | 2 | 4 | `11b143ff691cdc45aff2576764e7200d709a8a0913f8dd57204900f629a7fb72` |
| 2026-06-01T23:30:00+00:00 | 19 | 9 | 3 | `c96d444ab9ae5b95ff714f972e0818a02d10e4c3dd7e423f5cff163d77a8d2e8` |
| 2026-06-02T01:00:00+00:00 | 24 | 12 | 0 | `c40e3c81f90b1842238792b7c8c406cc492efae356279bca52ce0561c27a912e` |
| 2026-06-02T22:30:00+00:00 | 22 | 10 | 2 | `cce3a060c2aaf45838ed50d0e9d60803e5979a6900f7d26b73dce154eb58205e` |
| 2026-06-02T23:00:00+00:00 | 23 | 10 | 2 | `f9d4d345ffc6c384d0b61bca046fbb677ec6ca252f23ffe2db7433c70c8fe806` |
| 2026-06-03T01:00:00+00:00 | 28 | 12 | 0 | `80ee44949f77d59d66bb579e121cb682e99d70b4832476a9a175b7ba6a4ae6d0` |
| 2026-06-03T01:30:00+00:00 | 33 | 12 | 0 | `04f015052bb1831d14c0712481d96b1d911b98b93665797dd10817882cffa93e` |
| 2026-06-03T22:30:00+00:00 | 15 | 8 | 0 | `4e8f25a5c67fdd8175a0af1be9019e5483d3726ae1becdb208ca17ee94dd0bd1` |
| 2026-06-03T23:00:00+00:00 | 16 | 8 | 0 | `5dcb154aa072603984e43f23b82b940b4c16cf064bb13aceeb03b2c7d209e130` |
| 2026-06-04T01:00:00+00:00 | 17 | 8 | 0 | `24aefc6145e575f9ba8ecd6da36c56620245840d032a35ef77f8bb9c5032d6bb` |
| 2026-06-04T01:30:00+00:00 | 17 | 8 | 0 | `70f51cf28a3e6491537fb23c2e14ce17d8a86743da2ac1aa347e0edef2ac8ff6` |
| 2026-06-04T22:00:00+00:00 | 15 | 6 | 4 | `b491f297eadcafd67f311ccc404560090520d1c742d5496b615fc2c7f290113d` |
| 2026-06-04T22:30:00+00:00 | 15 | 6 | 4 | `618b3b8d0cb819528251eec81ed062cca576935b385d2ab262caa75086d284b2` |
| 2026-06-05T00:30:00+00:00 | 25 | 10 | 0 | `4d32ca4a998be9b67e3619cc1e1bde95915b06b7d1c9c3a883a85936e759a3b3` |
| 2026-06-05T22:30:00+00:00 | 25 | 12 | 2 | `5c4570011bf15766559ea4304e974f7d496fe6aef556504e81b53051599f544a` |
| 2026-06-05T23:00:00+00:00 | 25 | 12 | 2 | `9709d53655244e34ea257ff1f2e35af320ca38e97d605f090714cc7cd853a271` |
| 2026-06-06T01:00:00+00:00 | 29 | 14 | 0 | `a5f20d88666bbd02060820f584f984eafeb9e0a876d12c827de1baccfd9278f6` |
| 2026-06-06T01:30:00+00:00 | 31 | 14 | 0 | `859e32ebad7f92515054dbfeebd744bc226d2295c7c921c78cf21cf4786864ab` |
| 2026-06-06T16:30:00+00:00 | 17 | 8 | 4 | `ace92891a613c4e13a9d8498a352c9e1ac84b69510af0733365cad48cedfc44e` |
| 2026-06-06T23:00:00+00:00 | 30 | 10 | 2 | `be89c0ff42692d01deb9d79eea104e624a3ced64b83d3aaa7b19b1f8c4e8b602` |
| 2026-06-07T18:00:00+00:00 | 11 | 5 | 5 | `8b017da38e6a1b59ebbfbb8682698145725a3211f5386955000c865e069a3dbe` |
| 2026-06-07T18:30:00+00:00 | 13 | 5 | 4 | `0c761fadb96b1be1cbd42e5372e8e917c59df6654405244b9303ed6c573daa35` |
| 2026-06-07T22:00:00+00:00 | 18 | 8 | 2 | `3bfb6394c9b46f8ce91c64bf46cdc52046461e4022978327cc7a1221938fe8a1` |
| 2026-06-08T22:00:00+00:00 | 23 | 10 | 2 | `dee843733eeb588005796ccb9f97566cbc037f67709e42e2b93b886532ef0b78` |
| 2026-06-08T22:30:00+00:00 | 27 | 10 | 2 | `0ce37a3b608f02daf80916a1ad9169cef25828e1e58573fa899423f499583a86` |
| 2026-06-09T01:30:00+00:00 | 31 | 12 | 0 | `29ed481410d4db7820732bc272981d928fe7e0017b16633775c292c375fb90d2` |
| 2026-06-09T22:00:00+00:00 | 20 | 7 | 2 | `3925e2acfee232b70525a4d4c1c5b7fb260ff0a6af111e5b40ebcb2c52251bff` |
| 2026-06-09T22:30:00+00:00 | 20 | 7 | 2 | `f0a5b7c4cf401045391389e5db7f0b507e5122152b8e3f34739e58d140459276` |
| 2026-06-09T23:00:00+00:00 | 23 | 8 | 2 | `12a7252486038ce8f6b36f28afec4c3dca0894e424d9d5b0aed4b6c8445498fc` |
| 2026-06-10T01:00:00+00:00 | 25 | 10 | 0 | `8de0275c75a509de16c008c30c35f2861502d2c7629e389a5dbc6561c8f37721` |
| 2026-06-10T01:30:00+00:00 | 28 | 10 | 0 | `75b3f947fdd2e409b3796170757451b58c755ad16fe42590e930c46e984790e7` |
| 2026-06-10T22:30:00+00:00 | 17 | 10 | 2 | `c622b756566673feed538eabacd8cbe38c40a7bd8cc1e94610a710380f98103f` |
| 2026-06-11T01:30:00+00:00 | 21 | 11 | 0 | `aa5767ee1b6f63b79e90b6b192e4a4448c9e6160fa241c163b515e29c355c462` |
| 2026-06-11T22:00:00+00:00 | 19 | 10 | 2 | `5f683e5222ee007b050ba2cd99590daa8c1a5a77714807143697bc57620d9078` |
| 2026-06-11T22:30:00+00:00 | 20 | 10 | 2 | `526c1b20c6f3a9fb6523f22831b9ef112fed518c33f13b75db25c92823423d09` |
| 2026-06-12T00:00:00+00:00 | 22 | 11 | 1 | `ad24da50f7308ebbadcb9d7d1077d33416215a21e1e2e317955bb7c4a2e204e9` |
| 2026-06-12T01:00:00+00:00 | 24 | 12 | 0 | `641b0566091c30e0699f9c4d083a8e230414d64a5753d612cbcc6284719dc1b4` |
| 2026-06-13T01:00:00+00:00 | 19 | 12 | 0 | `c7b92310922e3ad3e852b2f7708b1ad2b4873c982c460f9284a581c49bdeeb91` |
| 2026-06-13T21:00:00+00:00 | 18 | 12 | 0 | `84d085cd3844118ab574cb59796701b2d8fcb9ec8dcd73cd0a4b7ce70597f054` |
| 2026-06-13T21:30:00+00:00 | 18 | 12 | 0 | `632e382682580df3b9daa345302922eec54eb9c59218f55b15a06b2c7b8e097f` |
| 2026-06-13T23:30:00+00:00 | 19 | 12 | 0 | `fd48e4b4a5c652340282722a8ea2d26956d12c2a06704cb3c281f1ffd70190f5` |
| 2026-06-14T00:00:00+00:00 | 20 | 12 | 0 | `e49e0e56ef717dd47cb52739d0c230cc120d9049c286c9e08ee0c657ddda0780` |
| 2026-06-14T01:00:00+00:00 | 20 | 12 | 0 | `1d4318fd062346854c708981787799a21dbc7e757221f5f8c295427b94197704` |
| 2026-06-14T18:00:00+00:00 | 5 | 4 | 6 | `7b9e64df4a870bb82e9ed5affa03f04a866e77030bcbd892a75ec476fa877e0a` |
| 2026-06-16T01:00:00+00:00 | 18 | 8 | 0 | `360caff19ad0feaa9123b6e69a4792028061dce8a33621d2aea5327301e3bdbd` |
| 2026-06-16T22:00:00+00:00 | 11 | 6 | 7 | `9e733503e2b600ab9c2f6bfc0ea1e21da40b9495c7b611e8a6cee25cfdc63543` |
| 2026-06-16T22:30:00+00:00 | 13 | 6 | 7 | `8ec64e84437d0605a0cb162485043f736487e5ed7d05993a402c84204b7123b3` |
| 2026-06-17T22:30:00+00:00 | 27 | 13 | 0 | `1ef6e99b7a95f6c2e6eff1047218b7c75ccce51ff60b3d07dbe79b3d3b98a4ef` |
| 2026-06-17T23:30:00+00:00 | 31 | 14 | 0 | `c1c85ce2e8916ccc8f76f25b7a4a7369c637764114568cca8e6a886f2acc4fda` |
| 2026-06-18T01:00:00+00:00 | 33 | 14 | 0 | `c8c181ebaade0c50b44d121ba357ff076fb983598ce125c4c423abf99e7467fb` |
| 2026-06-18T22:30:00+00:00 | 12 | 6 | 2 | `2b27b8d94a4137bc8e768bf090fb703dbba4ae3c380149eac19bc4c7443ce1dd` |
| 2026-06-19T22:30:00+00:00 | 19 | 10 | 2 | `fa5c9cded53fd0e893be4f949189bc4c769ae5e7bc06e59f9e4555b1b06fb94d` |
| 2026-06-19T23:00:00+00:00 | 20 | 10 | 2 | `7ea750c431071bece6cad6e7d791a7f0b8babb2f5e2f0cbd42dd08b39b5c1a9e` |
| 2026-06-20T01:00:00+00:00 | 27 | 12 | 0 | `7389501ae881ce09213ed65216efd12d5be786082dd3566477ebfb7a2ad3d3e0` |
| 2026-06-20T01:30:00+00:00 | 29 | 12 | 0 | `1220eb696a86795207187c1f8c802c686122b80a99e6dfe345397ef2639f02f3` |
| 2026-06-20T16:30:00+00:00 | 17 | 6 | 6 | `90565fe39f2720c03e39671a5b5d041e4dd5a211d795ef60a459279dde910415` |
| 2026-06-20T23:00:00+00:00 | 23 | 8 | 4 | `5275c9e177de3ec74e7542d0964728fd26e9afe9b2dca1f7397ec23c8ccc5a8d` |
| 2026-06-20T23:30:00+00:00 | 23 | 8 | 4 | `03d488eaf497019eb84df233dee76f3eafe48fccfef0cf94f6d277068a8e3978` |
| 2026-06-21T19:00:00+00:00 | 8 | 6 | 8 | `2a43d21c70b8e7a882fc7920ee6c661ed95803ee269882bf9ccfd6902a63f4bd` |
| 2026-06-21T21:30:00+00:00 | 24 | 12 | 2 | `63d2dfb79ec80c4958c27a6b86b473849dde1da60654f9e21288cf6688b554d2` |
| 2026-06-21T23:00:00+00:00 | 26 | 12 | 2 | `93882bcfcd96f91f922257b2b9bcdb720f957de9fc2caa4c751dcd6552f554b9` |
| 2026-06-21T23:30:00+00:00 | 26 | 12 | 2 | `2d1db6f445c8c5ca091a1012ef9be3944c37a44b5547aea64d68c0ee2024de1e` |
| 2026-06-22T22:00:00+00:00 | 19 | 8 | 2 | `4262ec7dee58255b876f4a84c5d525b2bcee56079db9297000d084148cff3021` |
| 2026-06-22T23:30:00+00:00 | 23 | 8 | 2 | `ce410eeca1381af855d8abc610fae0a0eb65d20e11283201d3e2636c911b33db` |
| 2026-06-23T01:00:00+00:00 | 27 | 10 | 0 | `2116ece9273b976f825d195050ecdbce890913754b9b00655935e8ed333b6091` |
| 2026-06-23T01:30:00+00:00 | 28 | 10 | 0 | `380f84955185a22ae5a2594c9edd154a668de3f10caf0cd0a70c677ac496b2bf` |
| 2026-06-24T01:00:00+00:00 | 17 | 10 | 0 | `381f1dda1a4c6ccde38e6379d2664818daf45f2239fade912adc1567d3e493f2` |
| 2026-06-24T23:30:00+00:00 | 27 | 10 | 4 | `a4e87f0afb80fef32ad417dfa9fd38b79d817cbc131ec9868c9a35b2c0247ab8` |
| 2026-06-25T01:00:00+00:00 | 37 | 14 | 0 | `044b5f22a06e3ff40f83f6f703a32a2fdddc295c1bea89cbce0d3c853dbbd5a8` |
| 2026-06-25T01:30:00+00:00 | 39 | 14 | 0 | `1703d9e4e8992a56a74e57482a043c4f6872f81941b583e8800a695683d587cc` |
| 2026-06-25T22:30:00+00:00 | 22 | 10 | 2 | `dbf8390cf5e9db1c08faca0dd66fe4a78389f11565b12bf6143382c45c4c89ca` |
| 2026-06-26T01:00:00+00:00 | 26 | 12 | 0 | `4cc1d6cbb5c99d5248d41410fabe41c13529a2c1492ffe196f5ba07552f30dbc` |
| 2026-06-26T01:30:00+00:00 | 28 | 12 | 0 | `ead0955e121258b29e77396b3e05034fdc67f24f59e27a19af5170e093a29f6f` |
| 2026-06-26T22:30:00+00:00 | 19 | 9 | 2 | `5c39e39185b75e1106c4c81741188e40000ec994eaeca4d45822d635e32d0084` |
| 2026-06-26T23:00:00+00:00 | 21 | 9 | 2 | `1bdacd0e624af75840c46d39435bd4114c0ab7282b34020941fccd619f032062` |
| 2026-06-27T01:00:00+00:00 | 24 | 10 | 1 | `fdbacb1ee608426725536bf6645628603ee3f8a112e8dd7b230ef86687d90ac3` |
| 2026-06-27T17:00:00+00:00 | 13 | 6 | 8 | `9a5590798593ef69b8c9828ef5f9044866420c1c1eadccd17089e2b56a7692bc` |
| 2026-06-27T17:30:00+00:00 | 13 | 6 | 8 | `62b4b2766872df252de8d27d5ed074c211a52a965611cdfd6c2dc49812a39e80` |
| 2026-06-27T23:00:00+00:00 | 28 | 12 | 2 | `8a1b00c904d4f7dd35da7a9bc988aa453551bf8106bfa358d17dd57a7caee6b8` |
| 2026-06-28T00:00:00+00:00 | 31 | 14 | 0 | `fa78386dd3385c6654bde06f05bc05a9ffc6b2d7ffd0ad650d4cb414c954fe05` |
| 2026-06-28T00:30:00+00:00 | 31 | 14 | 0 | `f4c1ddd7c9a0cbba8a8253e8fb77fc0740a6e6f84e65e1a8b18f4aea0afb4c4c` |
| 2026-06-28T17:00:00+00:00 | 15 | 8 | 0 | `892a8493d47b5b2f2715ac96b37be46f3e5c13824d1ec105bbf20f34879b1947` |
| 2026-06-28T17:30:00+00:00 | 15 | 8 | 0 | `8309a247ff7d9d2f8566bfe7a79cbe9c57134038f7d5bbc97ab9eb46efb4181b` |
| 2026-06-28T19:30:00+00:00 | 15 | 8 | 0 | `c4a04b8b985407a6a7f8e4448eb67c6acf9ecfb8e83b66275f5362da47f67c35` |
| 2026-06-28T22:00:00+00:00 | 15 | 8 | 0 | `4b88c4b66a4437c368f1d0d2e1adbfca80813f23669dca3afc14eeb446a0b6b8` |
| 2026-06-28T22:30:00+00:00 | 20 | 8 | 0 | `bf51e3ce1f5c4208b52d5ab1e2a78c7c40acff1ba2bd0978167c189fdd310552` |
| 2026-06-30T22:30:00+00:00 | 4 | 2 | 0 | `e8c70fc45544705137807e3f0e3f51f3d3e9eb7e713e35d3d7b43dfa91405492` |
| 2026-07-02T23:00:00+00:00 | 19 | 8 | 1 | `e087f1c724c8ed93de47a7eb46feb209d5dd370546a8b0be72b0998dd9f028a3` |
| 2026-07-03T01:30:00+00:00 | 23 | 10 | 0 | `2a0a1760bdf486e8038ec3e2b226fdd88df65573343e98fe637f56d0e794dcdb` |
| 2026-07-03T22:30:00+00:00 | 12 | 6 | 5 | `9d9e2fa8b263deb453ac2fb627873dea220abfaf006b1422939032e10ee114cd` |
| 2026-07-03T23:00:00+00:00 | 12 | 6 | 5 | `e83d9c2fb315f375168644198f0dfb4be8f78adb81fdfdcc2ae7330b0c42b404` |
| 2026-07-04T01:00:00+00:00 | 16 | 8 | 3 | `512b6f98846802f10ff51734803311db5601b0c9293ae09f587686c796e5e6b4` |
| 2026-07-04T16:00:00+00:00 | 6 | 4 | 8 | `38cf44c9ef65430597acb7a4b7445f1ac9166208e9f524a1d760d1a67d083616` |
| 2026-07-04T16:30:00+00:00 | 8 | 4 | 8 | `1f903d3905052017fbe060363a2175542562c57932d021c884444ce70aec1e24` |
| 2026-07-05T00:00:00+00:00 | 15 | 8 | 4 | `e9c6c0c0d7cd4f677f5011735807cf174eaeb0b271948345004d243a91f8c465` |
| 2026-07-05T00:30:00+00:00 | 17 | 8 | 4 | `489083b4c92b1e7ed90a16afd0b8581866e68ed04a92b3b895489871655d3d2c` |
| 2026-07-05T18:00:00+00:00 | 9 | 5 | 9 | `f01d2178b150060c2c9692a19df549f4ce3ef95e7eacf183dbbc817070ec06f4` |
| 2026-07-05T18:30:00+00:00 | 9 | 5 | 9 | `d11805edde8ba73856d1255246cb43e0d8d9f50837baddfaf54c9ddcaa820303` |
| 2026-07-05T22:00:00+00:00 | 16 | 9 | 6 | `8cb0118853d06cc4b88004d8c6c3f6f03e6d08f560c6b01516415583d283e798` |
| 2026-07-05T22:30:00+00:00 | 16 | 9 | 6 | `e9ef256cf82ed8edfd6f14919d71cce900cf79a65ef10c21b1a7fdbbc0f62498` |
| 2026-07-06T22:30:00+00:00 | 12 | 8 | 2 | `81601b3a635121da0723e03d73816d67aa0e1c5c2416ecf64413548d5ee81c56` |
| 2026-07-06T23:00:00+00:00 | 12 | 8 | 2 | `848657a62a0b1806dc346819bf18f4b8beba32b871c4cd91f0ff7b7d9142a823` |
| 2026-07-06T23:30:00+00:00 | 15 | 9 | 1 | `924a2bb8fc86d1c63d8251b36616f136738bcea1c9e30c0c772c64781aec76ff` |
| 2026-07-07T01:00:00+00:00 | 19 | 10 | 0 | `48787ff445a31b44529325192f94a35a2e2539a86d92800c6e615df97e62a5f9` |
| 2026-07-07T23:00:00+00:00 | 13 | 8 | 2 | `338a050c4965a612e9202f40b87dc0beec637143a45771de82555e06e537285f` |
| 2026-07-07T23:30:00+00:00 | 13 | 8 | 2 | `4302d8d709e77fffde36cefb500fa1ac5a7233c933e2cacc604ff2d9cbb72622` |
| 2026-07-08T01:30:00+00:00 | 20 | 10 | 0 | `655fbce53b689de60b1ac6a81ab02840d00f4f3189409dc6da07729db8923ecb` |
| 2026-07-08T22:00:00+00:00 | 14 | 8 | 3 | `c3112236da182e72432cc420246e8bdb3c1e5b6b2b67226cc3ff2c4ec48e634d` |
| 2026-07-08T22:30:00+00:00 | 17 | 7 | 4 | `2376736a1aeb5b772557e8a5efc66cd31bfcc90d6b1fe15dc5086987e9264fd8` |
| 2026-07-09T01:30:00+00:00 | 30 | 10 | 1 | `8e3cdf870d92071afab8fcc4eb1c27db2de7c4998245ae1e87b656e290b9bb4c` |
| 2026-07-09T23:30:00+00:00 | 26 | 11 | 1 | `d9186c3c3b6ee7bec9b21cc8c323808cd68ce22829bfa4431ce918b0129f1050` |
| 2026-07-10T01:00:00+00:00 | 28 | 12 | 0 | `2805da8cb4db60e87b391c9045e15634bef3db7a752d613b0f1e0a4cfa7375bc` |
| 2026-07-10T01:30:00+00:00 | 31 | 12 | 0 | `d11ea53ed553fe1abd325e7f101fa90d947ae3ed07463cf7363d562451593af5` |
| 2026-07-10T22:30:00+00:00 | 24 | 10 | 2 | `6629126539c7710fcbfd8a3de51f2a517160f7e703b08e09d0e7f81493801d82` |
| 2026-07-11T01:00:00+00:00 | 34 | 12 | 0 | `a804760df51be8708f03407808a4e42034de524723d86816989fcf3c479c7281` |
| 2026-07-11T01:30:00+00:00 | 34 | 12 | 0 | `e234eb02be1e24d71242b0244bcc8b47926f08aaa7167cf15a27d16cf75366e2` |
| 2026-07-11T19:00:00+00:00 | 20 | 5 | 7 | `1421b05cf8caa3ebd1bf03ebe9aab96c655d1863111f48a0171bfe45ce834bc3` |
| 2026-07-11T21:00:00+00:00 | 30 | 7 | 5 | `fea9d25e0e7f026e917153b9056dd3bd93e670369b2c578da274a9f58656a24e` |
| 2026-07-11T21:30:00+00:00 | 32 | 7 | 5 | `df313c5530b7b11a6c72d7e2a17bbd48bc57f9a9ab7efa105c630fa0dfce3975` |
| 2026-07-12T18:00:00+00:00 | 13 | 7 | 4 | `7bacba0b5487848ba56b2ae7b579856d5d78518da20582bad52afcca7f82b831` |
| 2026-07-12T18:30:00+00:00 | 14 | 8 | 4 | `32d6c10e6252bc22404beb955f2746038521dde50b589f1816d0867b75acd209` |
| 2026-07-12T22:30:00+00:00 | 28 | 12 | 0 | `6571ad9fb5bae1b2090ee2e6e908fb157808c2a53a816c34615e1b7f695f83fc` |
| 2026-07-13T22:00:00+00:00 | 16 | 8 | 0 | `c815d5a6d81cfd71f5adc21955a58ef73d98076ec1b7161625816e94858c4754` |
| 2026-07-13T22:30:00+00:00 | 16 | 8 | 0 | `3a41acd868b35cbeda97b861317d45a5f19fbe49c3542e4b5034a6fcacf652f0` |
| 2026-07-14T00:30:00+00:00 | 18 | 8 | 0 | `cb68a364f670608e7f6d13e2a39a3e6c9d3dc52862e72f7b4cefc583387f63a5` |
| 2026-07-14T14:00:00+00:00 | 8 | 4 | 6 | `2645b1c5732ddd9aa71dd90e087d2c883a6f4c1d993f38a300588a4cd421bdb9` |
| 2026-07-15T15:00:00+00:00 | 12 | 6 | 4 | `7c3b6fd21d658f0a869e1a058ad4c5e5f7ea384cca3062a3d38297c24bb0c528` |
| 2026-07-15T16:00:00+00:00 | 14 | 6 | 4 | `9418f6d120f737b7324a409244f71c66205a3694e0a0c6881fbb8a3594f08412` |
| 2026-07-15T16:30:00+00:00 | 14 | 6 | 4 | `2694da31d01091452a5a30c7f836f64f0e1c969270a38eda50fc39bd17db2347` |
| 2026-07-15T23:30:00+00:00 | 22 | 10 | 0 | `f031eab550327e409ee988478835f0383b47e43af37eb4ffec9b4cc6387bd8c3` |
| 2026-07-16T22:00:00+00:00 | 18 | 8 | 2 | `a40d7a7d25a97fc7756562721575af0c62e50ae11ed5a2d46bf63d51b6950603` |
| 2026-07-16T22:30:00+00:00 | 20 | 8 | 2 | `f92f16addee4666113705f2d246f40313932187d3f3a9e0127f320c1f671b44c` |
| 2026-07-17T00:00:00+00:00 | 25 | 10 | 0 | `8450f100b81793267ac1b86914e5766a3fe29820041a3f78edb4792be49eba9f` |
| 2026-07-17T00:30:00+00:00 | 25 | 10 | 0 | `5052bcd157e741c1e5989538ecae6ae429982093e3f71e4ffb0cca6315d301d9` |
| 2026-07-17T22:30:00+00:00 | 29 | 10 | 3 | `c9dc49703738950cb0b7eeda19b75b3bca91852706047c029f7205a2f5c9a658` |
| 2026-07-17T23:00:00+00:00 | 31 | 11 | 2 | `478a848cdaeb977cc2333e1714a81fcb8b8506208bd5112b6de5814e9fd26bb7` |
| 2026-07-18T01:30:00+00:00 | 34 | 13 | 0 | `53001b0f844b3376201bcb47ba0a513ee2703b4bd576e198118de9e9838d1340` |
| 2026-07-18T23:00:00+00:00 | 18 | 9 | 2 | `ed86550042bffc884e7f02f1dfb48c349f0641cb2409674d1ccd110d3894b0cb` |
| 2026-07-18T23:30:00+00:00 | 18 | 9 | 2 | `3690b7839c328625ec8462b6c2cd1451178a81994d5f6ecf86ed1081ad92de22` |
| 2026-07-19T19:00:00+00:00 | 20 | 7 | 6 | `7143db067dab27326d1c33dc44628914f260d2ed3bd55dc48df97367152a0f8c` |
| 2026-07-19T22:00:00+00:00 | 28 | 9 | 4 | `65eee2b8dd86f0f686b851e7aea18f44ac76d65fb28ca77aa921ff01c0221e32` |
| 2026-07-19T22:30:00+00:00 | 30 | 9 | 4 | `09679852c2bec17edc8ebb762c263f20514657bd7b9e7dc59bdcc99202d8b916` |

## Game-by-game model impact

`Gap` is positive when availability favors the home team. `Adjusted` uses the 1.0× availability scale.

| Date | Matchup | Score | Incumbent home P | Gap (pts) | Adjusted home P | Δ pp | Incumbent pick | Adjusted pick | Correct? | Baseline source | Availability status | Source conflicts | Report |
|---|---|---|---:|---:|---:|---:|---|---|---|---|---|---|---|
| 2026-05-14 | Minnesota Lynx @ Dallas Wings | 90-86 | 42.562% | -0.058 | 42.409% | -0.15% | away | away | yes | current artifact recomputation | complete | — | 2026-05-14T23:30:00+00:00 |
| 2026-05-15 | Las Vegas Aces @ Connecticut Sun | 101-94 | 30.974% | +0.379 | 31.883% | +0.91% | away | away | yes | current artifact recomputation | complete | — | 2026-05-15T23:00:00+00:00 |
| 2026-05-15 | Washington Mystics @ Indiana Fever | 104-102 | 69.608% | +0.000 | 69.608% | +0.00% | home | home | — | current artifact recomputation | fail_closed | — | 2026-05-15T23:00:00+00:00 |
| 2026-05-15 | Chicago Sky @ Phoenix Mercury | 83-91 | 68.321% | -0.024 | 68.264% | -0.06% | home | home | yes | current artifact recomputation | complete | — | 2026-05-16T01:30:00+00:00 |
| 2026-05-17 | Las Vegas Aces @ Atlanta Dream | 85-84 | 54.569% | -0.560 | 53.065% | -1.50% | home | home | no | current artifact recomputation | complete | — | 2026-05-17T17:00:00+00:00 |
| 2026-05-17 | Seattle Storm @ Indiana Fever | 78-89 | 62.681% | -0.904 | 60.343% | -2.34% | home | home | yes | current artifact recomputation | complete | — | 2026-05-17T21:30:00+00:00 |
| 2026-05-17 | Chicago Sky @ Minnesota Lynx | 86-79 | 74.221% | +0.855 | 76.053% | +1.83% | home | home | no | current artifact recomputation | complete | — | 2026-05-17T22:30:00+00:00 |
| 2026-05-18 | Washington Mystics @ Dallas Wings | 69-92 | 61.741% | +0.000 | 61.741% | +0.00% | home | home | — | current artifact recomputation | fail_closed | — | 2026-05-18T23:30:00+00:00 |
| 2026-05-20 | Dallas Wings @ Chicago Sky | 99-89 | 51.753% | -0.168 | 51.300% | -0.45% | home | home | no | current artifact recomputation | complete | — | 2026-05-21T00:30:00+00:00 |
| 2026-05-20 | Connecticut Sun @ Seattle Storm | 80-78 | 68.437% | -0.165 | 68.038% | -0.40% | home | home | no | current artifact recomputation | complete | — | 2026-05-21T01:30:00+00:00 |
| 2026-05-21 | Golden State Valkyries @ New York Liberty | 87-70 | 56.906% | +0.000 | 56.906% | +0.00% | home | home | — | current artifact recomputation | fail_closed | — | 2026-05-21T23:30:00+00:00 |
| 2026-05-21 | Los Angeles Sparks @ Phoenix Mercury | 97-88 | 63.767% | -0.344 | 62.891% | -0.88% | home | home | no | current artifact recomputation | complete | — | 2026-05-21T01:30:00+00:00 |
| 2026-05-22 | Dallas Wings @ Atlanta Dream | 69-86 | 63.838% | +0.000 | 63.838% | +0.00% | home | home | — | current artifact recomputation | fail_closed | — | 2026-05-22T23:00:00+00:00 |
| 2026-05-22 | Golden State Valkyries @ Indiana Fever | 82-90 | 60.569% | -0.341 | 59.678% | -0.89% | home | home | yes | current artifact recomputation | complete | — | 2026-05-22T23:00:00+00:00 |
| 2026-05-22 | Connecticut Sun @ Seattle Storm | 59-77 | 65.589% | -0.322 | 64.784% | -0.80% | home | home | yes | current artifact recomputation | complete | — | 2026-05-23T01:30:00+00:00 |
| 2026-05-23 | Minnesota Lynx @ Chicago Sky | 85-75 | 36.603% | -0.550 | 35.213% | -1.39% | away | away | yes | current artifact recomputation | complete | — | 2026-05-23T16:30:00+00:00 |
| 2026-05-23 | Los Angeles Sparks @ Las Vegas Aces | 101-95 | 69.046% | +0.331 | 69.831% | +0.79% | home | home | no | current artifact recomputation | complete | — | 2026-05-23T23:30:00+00:00 |
| 2026-05-24 | Phoenix Mercury @ Atlanta Dream | 80-82 | 67.829% | +0.000 | 67.829% | +0.00% | home | home | — | current artifact recomputation | fail_closed | — | 2026-05-24T18:30:00+00:00 |
| 2026-05-24 | Dallas Wings @ New York Liberty | 91-76 | 56.841% | +0.000 | 56.841% | +0.00% | home | home | — | current artifact recomputation | fail_closed | — | 2026-05-24T19:00:00+00:00 |
| 2026-05-24 | Washington Mystics @ Seattle Storm | 85-97 | 63.088% | +0.150 | 63.470% | +0.38% | home | home | yes | current artifact recomputation | complete | — | 2026-05-24T21:30:00+00:00 |
| 2026-05-25 | Connecticut Sun @ Golden State Valkyries | 70-97 | 72.484% | +0.122 | 72.759% | +0.27% | home | home | yes | current artifact recomputation | complete | — | 2026-05-26T01:30:00+00:00 |
| 2026-05-27 | Phoenix Mercury @ New York Liberty | 74-84 | 53.290% | +1.434 | 57.127% | +3.84% | home | home | yes | current artifact recomputation | complete | — | 2026-05-27T22:30:00+00:00 |
| 2026-05-27 | Atlanta Dream @ Minnesota Lynx | 81-96 | 58.468% | +0.000 | 58.468% | +0.00% | home | home | — | current artifact recomputation | fail_closed | — | 2026-05-28T00:30:00+00:00 |
| 2026-05-27 | Washington Mystics @ Seattle Storm | 78-64 | 65.855% | +0.259 | 66.495% | +0.64% | home | home | no | current artifact recomputation | complete | — | 2026-05-28T01:30:00+00:00 |
| 2026-05-28 | Las Vegas Aces @ Dallas Wings | 87-95 | 51.600% | +0.381 | 52.628% | +1.03% | home | home | yes | current artifact recomputation | complete | — | 2026-05-28T23:30:00+00:00 |
| 2026-05-28 | Indiana Fever @ Golden State Valkyries | 88-90 | 53.398% | +0.199 | 53.933% | +0.54% | home | home | yes | current artifact recomputation | complete | — | 2026-05-29T01:30:00+00:00 |
| 2026-05-29 | Phoenix Mercury @ New York Liberty | 68-75 | 57.395% | +1.253 | 60.690% | +3.30% | home | home | yes | current artifact recomputation | complete | — | 2026-05-29T23:00:00+00:00 |
| 2026-05-29 | Los Angeles Sparks @ Washington Mystics | 92-87 | 47.865% | +0.000 | 47.865% | +0.00% | away | away | — | current artifact recomputation | fail_closed | — | 2026-05-29T23:00:00+00:00 |
| 2026-05-29 | Minnesota Lynx @ Chicago Sky | 79-58 | 29.956% | -0.651 | 28.443% | -1.51% | away | away | yes | current artifact recomputation | complete | — | 2026-05-29T23:00:00+00:00 |
| 2026-05-29 | Atlanta Dream @ Portland Fire | 86-66 | 46.041% | +0.000 | 46.041% | +0.00% | away | away | — | current artifact recomputation | fail_closed | — | 2026-05-29T23:00:00+00:00 |
| 2026-05-30 | Seattle Storm @ Toronto Tempo | 72-93 | 58.857% | -0.300 | 58.064% | -0.79% | home | home | yes | current artifact recomputation | complete | — | 2026-05-30T16:30:00+00:00 |
| 2026-05-30 | Los Angeles Sparks @ Connecticut Sun | 81-84 | 37.256% | +1.055 | 39.987% | +2.73% | away | away | no | current artifact recomputation | complete | — | 2026-05-30T21:30:00+00:00 |
| 2026-05-30 | Indiana Fever @ Portland Fire | 84-100 | 47.501% | -0.937 | 44.983% | -2.52% | away | away | no | current artifact recomputation | complete | — | 2026-05-30T23:00:00+00:00 |
| 2026-05-31 | Las Vegas Aces @ Golden State Valkyries | 91-81 | 55.546% | +0.537 | 56.979% | +1.43% | home | home | no | current artifact recomputation | complete | — | 2026-05-31T19:00:00+00:00 |
| 2026-06-01 | Seattle Storm @ Dallas Wings | 56-79 | 68.435% | -0.925 | 66.179% | -2.26% | home | home | yes | current artifact recomputation | complete | — | 2026-06-01T23:30:00+00:00 |
| 2026-06-01 | Minnesota Lynx @ Phoenix Mercury | 111-77 | 36.512% | -0.786 | 34.532% | -1.98% | away | away | yes | current artifact recomputation | complete | — | 2026-06-02T01:00:00+00:00 |
| 2026-06-02 | Connecticut Sun @ Atlanta Dream | 75-91 | 75.138% | +0.000 | 75.138% | +0.00% | home | home | — | current artifact recomputation | fail_closed | — | 2026-06-02T23:00:00+00:00 |
| 2026-06-02 | Chicago Sky @ Washington Mystics | 72-90 | 61.062% | +0.823 | 63.180% | +2.12% | home | home | yes | current artifact recomputation | complete | — | 2026-06-02T23:00:00+00:00 |
| 2026-06-02 | Portland Fire @ Golden State Valkyries | 77-95 | 60.729% | +0.083 | 60.945% | +0.22% | home | home | yes | current artifact recomputation | complete | — | 2026-06-03T01:30:00+00:00 |
| 2026-06-02 | Las Vegas Aces @ Los Angeles Sparks | 79-69 | 47.609% | -1.403 | 43.845% | -3.76% | away | away | yes | current artifact recomputation | complete | — | 2026-06-03T01:30:00+00:00 |
| 2026-06-03 | Toronto Tempo @ New York Liberty | 82-97 | 59.007% | +0.410 | 60.083% | +1.08% | home | home | yes | current artifact recomputation | complete | — | 2026-06-03T23:00:00+00:00 |
| 2026-06-03 | Phoenix Mercury @ Seattle Storm | 72-68 | 55.508% | -1.620 | 51.151% | -4.36% | home | home | no | current artifact recomputation | complete | — | 2026-06-04T01:30:00+00:00 |
| 2026-06-04 | Atlanta Dream @ Indiana Fever | 71-83 | 48.405% | +0.000 | 48.405% | +0.00% | away | away | — | current artifact recomputation | fail_closed | — | 2026-06-04T22:30:00+00:00 |
| 2026-06-04 | Golden State Valkyries @ Minnesota Lynx | 84-87 | 68.555% | +0.029 | 68.626% | +0.07% | home | home | yes | current artifact recomputation | complete | — | 2026-06-05T00:30:00+00:00 |
| 2026-06-05 | Connecticut Sun @ Chicago Sky | 80-85 | 57.785% | -1.862 | 52.804% | -4.98% | home | home | yes | current artifact recomputation | complete | — | 2026-06-05T23:00:00+00:00 |
| 2026-06-05 | Dallas Wings @ Los Angeles Sparks | 104-96 | 48.093% | +1.798 | 52.943% | +4.85% | away | home | no | current artifact recomputation | complete | — | 2026-06-06T01:30:00+00:00 |
| 2026-06-05 | Phoenix Mercury @ Portland Fire | 78-72 | 56.834% | -1.313 | 53.319% | -3.51% | home | home | no | current artifact recomputation | complete | — | 2026-06-06T01:30:00+00:00 |
| 2026-06-06 | Seattle Storm @ Minnesota Lynx | 68-88 | 73.501% | +0.564 | 74.735% | +1.23% | home | home | yes | current artifact recomputation | complete | — | 2026-06-06T16:30:00+00:00 |
| 2026-06-06 | Golden State Valkyries @ Las Vegas Aces | 79-84 | 66.459% | -0.913 | 64.179% | -2.28% | home | home | yes | current artifact recomputation | complete | — | 2026-06-06T16:30:00+00:00 |
| 2026-06-06 | Washington Mystics @ Atlanta Dream | 77-109 | 71.379% | +0.427 | 72.354% | +0.98% | home | home | yes | current artifact recomputation | complete | — | 2026-06-06T16:30:00+00:00 |
| 2026-06-06 | Indiana Fever @ New York Liberty | 75-83 | 51.917% | -0.177 | 51.440% | -0.48% | home | home | yes | current artifact recomputation | complete | — | 2026-06-06T23:00:00+00:00 |
| 2026-06-07 | Chicago Sky @ Toronto Tempo | 68-85 | 66.580% | -0.210 | 66.060% | -0.52% | home | home | yes | current artifact recomputation | complete | — | 2026-06-07T18:30:00+00:00 |
| 2026-06-07 | Portland Fire @ Los Angeles Sparks | 72-89 | 59.845% | +0.000 | 59.845% | +0.00% | home | home | — | current artifact recomputation | fail_closed | — | 2026-06-07T22:00:00+00:00 |
| 2026-06-08 | New York Liberty @ Connecticut Sun | 89-80 | 34.060% | -0.705 | 32.328% | -1.73% | away | away | yes | current artifact recomputation | complete | — | 2026-06-08T22:30:00+00:00 |
| 2026-06-08 | Indiana Fever @ Washington Mystics | 78-76 | 40.826% | +0.000 | 40.826% | +0.00% | away | away | yes | current artifact recomputation | complete | — | 2026-06-08T22:30:00+00:00 |
| 2026-06-08 | Seattle Storm @ Las Vegas Aces | 91-101 | 73.560% | -1.132 | 70.995% | -2.57% | home | home | yes | current artifact recomputation | complete | — | 2026-06-09T01:30:00+00:00 |
| 2026-06-09 | Atlanta Dream @ Chicago Sky | 82-75 | 33.449% | +0.000 | 33.449% | -0.00% | away | away | — | current artifact recomputation | fail_closed | — | 2026-06-09T22:30:00+00:00 |
| 2026-06-09 | Dallas Wings @ Minnesota Lynx | 76-100 | 65.269% | +1.851 | 69.772% | +4.50% | home | home | yes | current artifact recomputation | complete | — | 2026-06-09T23:00:00+00:00 |
| 2026-06-09 | Phoenix Mercury @ Golden State Valkyries | 81-87 | 59.698% | +0.222 | 60.280% | +0.58% | home | home | yes | current artifact recomputation | complete | — | 2026-06-10T01:30:00+00:00 |
| 2026-06-10 | Connecticut Sun @ Toronto Tempo | 102-106 | 71.719% | -0.908 | 69.606% | -2.11% | home | home | yes | current artifact recomputation | complete | — | 2026-06-10T22:30:00+00:00 |
| 2026-06-10 | Los Angeles Sparks @ Seattle Storm | 88-83 | 43.725% | +0.195 | 44.246% | +0.52% | away | away | yes | current artifact recomputation | complete | — | 2026-06-11T01:30:00+00:00 |
| 2026-06-11 | Chicago Sky @ Indiana Fever | 106-114 | 74.100% | -0.003 | 74.095% | -0.01% | home | home | yes | current artifact recomputation | complete | — | 2026-06-11T22:30:00+00:00 |
| 2026-06-11 | New York Liberty @ Atlanta Dream | 104-90 | 62.079% | +0.000 | 62.079% | +0.00% | home | home | — | current artifact recomputation | fail_closed | — | 2026-06-11T22:30:00+00:00 |
| 2026-06-11 | Phoenix Mercury @ Dallas Wings | 70-85 | 65.984% | -0.246 | 65.372% | -0.61% | home | home | yes | current artifact recomputation | complete | — | 2026-06-12T00:00:00+00:00 |
| 2026-06-11 | Las Vegas Aces @ Portland Fire | 105-89 | 35.576% | +0.102 | 35.833% | +0.26% | away | away | yes | current artifact recomputation | complete | — | 2026-06-12T01:00:00+00:00 |
| 2026-06-12 | Toronto Tempo @ Washington Mystics | 85-86 | 48.423% | +1.958 | 53.704% | +5.28% | away | home | yes | current artifact recomputation | complete | — | 2026-06-12T01:00:00+00:00 |
| 2026-06-12 | Golden State Valkyries @ Seattle Storm | 76-72 | 38.384% | -0.106 | 38.109% | -0.27% | away | away | yes | current artifact recomputation | complete | — | 2026-06-13T01:00:00+00:00 |
| 2026-06-13 | Indiana Fever @ Connecticut Sun | 85-75 | 30.903% | -0.285 | 30.227% | -0.68% | away | away | yes | current artifact recomputation | complete | — | 2026-06-13T21:30:00+00:00 |
| 2026-06-13 | Minnesota Lynx @ Las Vegas Aces | 97-100 | 50.986% | -0.347 | 50.050% | -0.94% | home | home | yes | current artifact recomputation | complete | — | 2026-06-13T23:30:00+00:00 |
| 2026-06-13 | Dallas Wings @ Portland Fire | 83-84 | 36.699% | +2.438 | 43.058% | +6.36% | away | away | no | current artifact recomputation | complete | — | 2026-06-14T00:00:00+00:00 |
| 2026-06-13 | Los Angeles Sparks @ Phoenix Mercury | 111-102 | 52.486% | +0.000 | 52.486% | +0.00% | home | home | no | current artifact recomputation | complete | — | 2026-06-14T01:00:00+00:00 |
| 2026-06-14 | Washington Mystics @ New York Liberty | 64-86 | 70.462% | +0.245 | 71.031% | +0.57% | home | home | yes | current artifact recomputation | complete | — | 2026-06-14T18:00:00+00:00 |
| 2026-06-14 | Atlanta Dream @ Toronto Tempo | 102-77 | 48.467% | +0.000 | 48.467% | +0.00% | away | away | — | current artifact recomputation | fail_closed | — | 2026-06-14T18:00:00+00:00 |
| 2026-06-15 | Las Vegas Aces @ Dallas Wings | 66-96 | 50.687% | +0.000 | 50.687% | +0.00% | home | home | — | current artifact recomputation | fail_closed | — | 2026-06-14T18:00:00+00:00 |
| 2026-06-15 | Portland Fire @ Minnesota Lynx | 74-107 | 74.550% | +0.000 | 74.550% | +0.00% | home | home | — | current artifact recomputation | fail_closed | — | 2026-06-14T18:00:00+00:00 |
| 2026-06-15 | Los Angeles Sparks @ Golden State Valkyries | 58-78 | 60.731% | +0.000 | 60.731% | +0.00% | home | home | yes | current artifact recomputation | complete | — | 2026-06-16T01:00:00+00:00 |
| 2026-06-16 | Toronto Tempo @ Indiana Fever | 91-113 | 67.690% | +2.745 | 74.047% | +6.36% | home | home | yes | current artifact recomputation | complete | — | 2026-06-16T22:30:00+00:00 |
| 2026-06-17 | Washington Mystics @ Connecticut Sun | 88-81 | 42.978% | +1.082 | 45.869% | +2.89% | away | away | yes | current artifact recomputation | complete | — | 2026-06-17T22:30:00+00:00 |
| 2026-06-17 | New York Liberty @ Chicago Sky | 96-95 | 29.953% | +0.533 | 31.218% | +1.27% | away | away | yes | current artifact recomputation | complete | — | 2026-06-17T23:30:00+00:00 |
| 2026-06-17 | Dallas Wings @ Golden State Valkyries | 80-91 | 52.490% | +0.545 | 53.957% | +1.47% | home | home | yes | current artifact recomputation | complete | — | 2026-06-18T01:00:00+00:00 |
| 2026-06-17 | Minnesota Lynx @ Los Angeles Sparks | 99-83 | 39.431% | -2.708 | 32.587% | -6.84% | away | away | yes | current artifact recomputation | complete | — | 2026-06-18T01:00:00+00:00 |
| 2026-06-17 | Las Vegas Aces @ Phoenix Mercury | 86-76 | 36.175% | +0.232 | 36.766% | +0.59% | away | away | yes | current artifact recomputation | complete | — | 2026-06-18T01:00:00+00:00 |
| 2026-06-17 | Seattle Storm @ Portland Fire | 89-94 | 62.121% | +0.476 | 63.341% | +1.22% | home | home | yes | current artifact recomputation | complete | — | 2026-06-18T01:00:00+00:00 |
| 2026-06-18 | Atlanta Dream @ Indiana Fever | 108-101 | 55.916% | +0.000 | 55.916% | +0.00% | home | home | — | current artifact recomputation | fail_closed | — | 2026-06-18T22:30:00+00:00 |
| 2026-06-19 | Toronto Tempo @ Connecticut Sun | 101-97 | 38.889% | +3.561 | 48.356% | +9.47% | away | away | yes | current artifact recomputation | complete | — | 2026-06-19T23:00:00+00:00 |
| 2026-06-19 | Washington Mystics @ New York Liberty | 86-83 | 70.689% | -1.037 | 68.231% | -2.46% | home | home | no | current artifact recomputation | complete | — | 2026-06-19T23:00:00+00:00 |
| 2026-06-19 | Minnesota Lynx @ Golden State Valkyries | 81-75 | 47.139% | +0.098 | 47.402% | +0.26% | away | away | yes | current artifact recomputation | complete | — | 2026-06-20T01:30:00+00:00 |
| 2026-06-20 | Indiana Fever @ Atlanta Dream | 96-113 | 62.743% | +0.000 | 62.743% | +0.00% | home | home | — | current artifact recomputation | fail_closed | — | 2026-06-20T16:30:00+00:00 |
| 2026-06-20 | Seattle Storm @ Phoenix Mercury | 73-93 | 65.104% | -0.035 | 65.017% | -0.09% | home | home | yes | current artifact recomputation | complete | — | 2026-06-20T16:30:00+00:00 |
| 2026-06-20 | Chicago Sky @ Dallas Wings | 92-93 | 78.502% | +0.605 | 79.678% | +1.18% | home | home | yes | current artifact recomputation | complete | — | 2026-06-20T23:30:00+00:00 |
| 2026-06-21 | Golden State Valkyries @ Las Vegas Aces | 73-92 | 62.341% | -0.196 | 61.837% | -0.50% | home | home | yes | current artifact recomputation | complete | — | 2026-06-21T19:00:00+00:00 |
| 2026-06-21 | Washington Mystics @ Minnesota Lynx | 84-79 | 76.113% | +1.351 | 78.853% | +2.74% | home | home | no | current artifact recomputation | complete | — | 2026-06-21T21:30:00+00:00 |
| 2026-06-21 | New York Liberty @ Los Angeles Sparks | 97-98 | 49.405% | -1.463 | 45.466% | -3.94% | away | away | no | current artifact recomputation | complete | — | 2026-06-21T23:30:00+00:00 |
| 2026-06-22 | Chicago Sky @ Connecticut Sun | 63-92 | 50.319% | +0.521 | 51.726% | +1.41% | home | home | yes | current artifact recomputation | complete | — | 2026-06-22T22:00:00+00:00 |
| 2026-06-22 | Toronto Tempo @ Atlanta Dream | 87-94 | 69.884% | +2.552 | 75.608% | +5.72% | home | home | yes | current artifact recomputation | complete | — | 2026-06-22T22:00:00+00:00 |
| 2026-06-22 | Phoenix Mercury @ Indiana Fever | 77-86 | 63.613% | +0.791 | 65.602% | +1.99% | home | home | yes | current artifact recomputation | complete | — | 2026-06-22T23:30:00+00:00 |
| 2026-06-22 | Dallas Wings @ Seattle Storm | 112-110 | 29.912% | -0.919 | 27.788% | -2.12% | away | away | yes | current artifact recomputation | complete | — | 2026-06-23T01:30:00+00:00 |
| 2026-06-23 | New York Liberty @ Las Vegas Aces | 87-76 | 67.934% | +0.000 | 67.934% | +0.00% | home | home | no | current artifact recomputation | complete | — | 2026-06-24T01:00:00+00:00 |
| 2026-06-24 | Phoenix Mercury @ Indiana Fever | 111-109 | 66.210% | +0.099 | 66.454% | +0.24% | home | home | no | current artifact recomputation | complete | — | 2026-06-24T01:00:00+00:00 |
| 2026-06-24 | Minnesota Lynx @ Washington Mystics | 78-76 | 35.018% | +0.000 | 35.018% | +0.00% | away | away | yes | current artifact recomputation | complete | — | 2026-06-24T01:00:00+00:00 |
| 2026-06-24 | Portland Fire @ Chicago Sky | 78-101 | 42.462% | -0.784 | 40.395% | -2.07% | away | away | no | current artifact recomputation | complete | — | 2026-06-24T23:30:00+00:00 |
| 2026-06-24 | Atlanta Dream @ Golden State Valkyries | 66-77 | 52.307% | +0.073 | 52.504% | +0.20% | home | home | yes | current artifact recomputation | complete | — | 2026-06-25T01:30:00+00:00 |
| 2026-06-25 | Los Angeles Sparks @ Toronto Tempo | 97-125 | 50.479% | -1.522 | 46.374% | -4.10% | home | away | no | current artifact recomputation | complete | — | 2026-06-25T22:30:00+00:00 |
| 2026-06-25 | Dallas Wings @ Las Vegas Aces | 84-99 | 62.480% | +0.081 | 62.686% | +0.21% | home | home | yes | current artifact recomputation | complete | — | 2026-06-26T01:30:00+00:00 |
| 2026-06-25 | New York Liberty @ Seattle Storm | 88-99 | 28.389% | +2.627 | 34.696% | +6.31% | away | away | no | current artifact recomputation | complete | — | 2026-06-26T01:30:00+00:00 |
| 2026-06-26 | Washington Mystics @ Connecticut Sun | 57-68 | 36.754% | -0.841 | 34.632% | -2.12% | away | away | no | current artifact recomputation | complete | — | 2026-06-26T23:00:00+00:00 |
| 2026-06-26 | Portland Fire @ Chicago Sky | 94-124 | 50.777% | -1.539 | 46.627% | -4.15% | home | away | no | current artifact recomputation | complete | — | 2026-06-26T23:00:00+00:00 |
| 2026-06-26 | Atlanta Dream @ Golden State Valkyries | 75-78 | 56.732% | +0.000 | 56.732% | +0.00% | home | home | — | current artifact recomputation | fail_closed | — | 2026-06-27T01:00:00+00:00 |
| 2026-06-27 | Phoenix Mercury @ Toronto Tempo | 89-80 | 60.449% | -1.998 | 55.164% | -5.28% | home | home | no | current artifact recomputation | complete | — | 2026-06-27T17:30:00+00:00 |
| 2026-06-27 | Los Angeles Sparks @ Indiana Fever | 87-111 | 61.967% | +1.391 | 65.496% | +3.53% | home | home | yes | current artifact recomputation | complete | — | 2026-06-27T23:00:00+00:00 |
| 2026-06-27 | Atlanta Dream @ Seattle Storm | 90-105 | 29.696% | +0.703 | 31.363% | +1.67% | away | away | no | current artifact recomputation | complete | — | 2026-06-28T00:30:00+00:00 |
| 2026-06-28 | Minnesota Lynx @ Dallas Wings | 85-77 | 41.764% | -0.924 | 39.340% | -2.42% | away | away | yes | current artifact recomputation | complete | — | 2026-06-28T17:30:00+00:00 |
| 2026-06-28 | Portland Fire @ Washington Mystics | 123-124 | 68.776% | +0.524 | 70.019% | +1.24% | home | home | yes | current artifact recomputation | complete | — | 2026-06-28T17:30:00+00:00 |
| 2026-06-28 | Las Vegas Aces @ Chicago Sky | 107-99 | 26.639% | -0.052 | 26.524% | -0.11% | away | away | yes | current artifact recomputation | complete | — | 2026-06-28T19:30:00+00:00 |
| 2026-06-28 | New York Liberty @ Golden State Valkyries | 67-76 | 64.429% | +1.373 | 67.827% | +3.40% | home | home | yes | current artifact recomputation | complete | — | 2026-06-28T22:30:00+00:00 |
| 2026-06-30 | Las Vegas Aces @ New York Liberty | 85-93 | 45.481% | +3.285 | 54.330% | +8.85% | away | home | yes | current artifact recomputation | complete | — | 2026-06-30T22:30:00+00:00 |
| 2026-07-02 | Atlanta Dream @ Washington Mystics | 76-81 | 44.125% | -1.124 | 41.142% | -2.98% | away | away | no | current artifact recomputation | complete | — | 2026-07-02T23:00:00+00:00 |
| 2026-07-02 | Dallas Wings @ Connecticut Sun | 86-83 | 34.755% | +0.053 | 34.887% | +0.13% | away | away | yes | current artifact recomputation | complete | — | 2026-07-02T23:00:00+00:00 |
| 2026-07-02 | Seattle Storm @ Phoenix Mercury | 67-90 | 66.922% | -0.537 | 65.593% | -1.33% | home | home | yes | current artifact recomputation | complete | — | 2026-07-03T01:30:00+00:00 |
| 2026-07-03 | Minnesota Lynx @ New York Liberty | 86-99 | 39.205% | -1.137 | 36.281% | -2.92% | away | away | no | current artifact recomputation | complete | — | 2026-07-03T23:00:00+00:00 |
| 2026-07-03 | Chicago Sky @ Las Vegas Aces | 90-98 | 78.328% | -3.561 | 70.622% | -7.71% | home | home | yes | current artifact recomputation | complete | — | 2026-07-04T01:00:00+00:00 |
| 2026-07-04 | Golden State Valkyries @ Atlanta Dream | 88-83 | 45.308% | -0.024 | 45.244% | -0.06% | away | away | yes | current artifact recomputation | complete | — | 2026-07-04T16:30:00+00:00 |
| 2026-07-04 | Portland Fire @ Seattle Storm | 77-72 | 61.844% | +2.237 | 67.465% | +5.62% | home | home | no | current artifact recomputation | complete | — | 2026-07-05T00:30:00+00:00 |
| 2026-07-05 | Dallas Wings @ Toronto Tempo | 89-76 | 42.876% | -0.898 | 40.504% | -2.37% | away | away | yes | current artifact recomputation | complete | — | 2026-07-05T18:30:00+00:00 |
| 2026-07-05 | Indiana Fever @ Las Vegas Aces | 84-68 | 66.740% | -2.114 | 61.397% | -5.34% | home | home | no | current artifact recomputation | complete | — | 2026-07-05T22:30:00+00:00 |
| 2026-07-06 | Golden State Valkyries @ Washington Mystics | 62-49 | 36.525% | -1.480 | 32.830% | -3.69% | away | away | yes | current artifact recomputation | complete | — | 2026-07-06T23:00:00+00:00 |
| 2026-07-06 | Connecticut Sun @ Minnesota Lynx | 90-89 | 78.216% | -1.315 | 75.508% | -2.71% | home | home | no | current artifact recomputation | complete | — | 2026-07-06T23:30:00+00:00 |
| 2026-07-06 | Seattle Storm @ Los Angeles Sparks | 82-64 | 64.352% | -3.050 | 56.414% | -7.94% | home | home | no | current artifact recomputation | complete | — | 2026-07-07T01:00:00+00:00 |
| 2026-07-07 | Dallas Wings @ New York Liberty | 88-77 | 56.100% | -2.111 | 50.424% | -5.68% | home | home | no | current artifact recomputation | complete | — | 2026-07-07T23:30:00+00:00 |
| 2026-07-07 | Chicago Sky @ Phoenix Mercury | 77-66 | 72.331% | -0.263 | 71.733% | -0.60% | home | home | no | current artifact recomputation | complete | — | 2026-07-08T01:30:00+00:00 |
| 2026-07-08 | Golden State Valkyries @ Toronto Tempo | 83-75 | 31.112% | -2.419 | 25.579% | -5.53% | away | away | yes | current artifact recomputation | complete | — | 2026-07-08T22:30:00+00:00 |
| 2026-07-08 | Minnesota Lynx @ Connecticut Sun | 86-80 | 27.173% | +1.141 | 29.793% | +2.62% | away | away | yes | current artifact recomputation | complete | — | 2026-07-08T22:30:00+00:00 |
| 2026-07-08 | Indiana Fever @ Los Angeles Sparks | 92-106 | 37.973% | +0.000 | 37.973% | -0.00% | away | away | — | current artifact recomputation | fail_closed | — | 2026-07-09T01:30:00+00:00 |
| 2026-07-09 | Seattle Storm @ Atlanta Dream | 78-89 | 66.298% | +0.091 | 66.523% | +0.22% | home | home | yes | current artifact recomputation | complete | — | 2026-07-09T23:30:00+00:00 |
| 2026-07-09 | Indiana Fever @ Phoenix Mercury | 92-89 | 43.419% | -0.246 | 42.765% | -0.65% | away | away | yes | current artifact recomputation | complete | — | 2026-07-10T01:30:00+00:00 |
| 2026-07-09 | Las Vegas Aces @ Portland Fire | 88-80 | 31.484% | -0.919 | 29.310% | -2.17% | away | away | yes | current artifact recomputation | complete | — | 2026-07-10T01:30:00+00:00 |
| 2026-07-10 | Golden State Valkyries @ Connecticut Sun | 79-64 | 24.265% | -0.589 | 23.037% | -1.23% | away | away | yes | current artifact recomputation | complete | — | 2026-07-10T22:30:00+00:00 |
| 2026-07-10 | Dallas Wings @ Toronto Tempo | 108-95 | 34.981% | -2.427 | 29.113% | -5.87% | away | away | yes | current artifact recomputation | complete | — | 2026-07-10T22:30:00+00:00 |
| 2026-07-10 | Chicago Sky @ Los Angeles Sparks | 87-102 | 62.913% | -3.188 | 54.530% | -8.38% | home | home | yes | current artifact recomputation | complete | — | 2026-07-11T01:30:00+00:00 |
| 2026-07-11 | New York Liberty @ Minnesota Lynx | 85-90 | 66.462% | +3.573 | 74.758% | +8.30% | home | home | yes | current artifact recomputation | complete | — | 2026-07-11T01:30:00+00:00 |
| 2026-07-11 | Portland Fire @ Atlanta Dream | 102-92 | 71.823% | -0.412 | 70.874% | -0.95% | home | home | no | current artifact recomputation | complete | — | 2026-07-11T19:00:00+00:00 |
| 2026-07-11 | Phoenix Mercury @ Las Vegas Aces | 58-106 | 71.683% | +0.000 | 71.683% | +0.00% | home | home | — | current artifact recomputation | fail_closed | — | 2026-07-11T21:30:00+00:00 |
| 2026-07-12 | New York Liberty @ Toronto Tempo | 91-93 | 40.087% | +2.491 | 46.712% | +6.62% | away | away | no | current artifact recomputation | complete | — | 2026-07-12T18:30:00+00:00 |
| 2026-07-12 | Seattle Storm @ Washington Mystics | 79-84 | 63.821% | +0.948 | 66.197% | +2.38% | home | home | yes | current artifact recomputation | complete | — | 2026-07-12T18:30:00+00:00 |
| 2026-07-12 | Chicago Sky @ Dallas Wings | 91-96 | 77.310% | +0.106 | 77.525% | +0.22% | home | home | yes | current artifact recomputation | complete | — | 2026-07-12T22:30:00+00:00 |
| 2026-07-12 | Indiana Fever @ Las Vegas Aces | 109-75 | 64.279% | +0.004 | 64.289% | +0.01% | home | home | no | current artifact recomputation | complete | — | 2026-07-12T22:30:00+00:00 |
| 2026-07-13 | Los Angeles Sparks @ Atlanta Dream | 92-101 | 60.107% | +2.514 | 66.503% | +6.40% | home | home | yes | current artifact recomputation | complete | — | 2026-07-13T22:30:00+00:00 |
| 2026-07-13 | Phoenix Mercury @ Minnesota Lynx | 100-104 | 74.059% | +2.062 | 78.369% | +4.31% | home | home | yes | current artifact recomputation | complete | — | 2026-07-14T00:30:00+00:00 |
| 2026-07-14 | Portland Fire @ Connecticut Sun | 87-90 | 50.863% | -0.588 | 49.277% | -1.59% | home | away | no | current artifact recomputation | complete | — | 2026-07-14T14:00:00+00:00 |
| 2026-07-14 | Washington Mystics @ Toronto Tempo | 79-62 | 50.816% | -0.862 | 48.490% | -2.33% | home | away | yes | current artifact recomputation | complete | — | 2026-07-14T14:00:00+00:00 |
| 2026-07-15 | Seattle Storm @ Chicago Sky | 90-95 | 53.842% | +0.694 | 55.702% | +1.86% | home | home | yes | current artifact recomputation | complete | — | 2026-07-15T15:00:00+00:00 |
| 2026-07-15 | Los Angeles Sparks @ Minnesota Lynx | 87-96 | 74.913% | +1.371 | 77.772% | +2.86% | home | home | yes | current artifact recomputation | complete | — | 2026-07-15T16:30:00+00:00 |
| 2026-07-15 | Golden State Valkyries @ Indiana Fever | 88-75 | 46.352% | +0.000 | 46.352% | +0.00% | away | away | yes | current artifact recomputation | complete | — | 2026-07-15T23:30:00+00:00 |
| 2026-07-16 | Portland Fire @ Washington Mystics | 75-56 | 70.090% | +1.339 | 73.157% | +3.07% | home | home | no | current artifact recomputation | complete | — | 2026-07-16T22:30:00+00:00 |
| 2026-07-16 | New York Liberty @ Dallas Wings | unsettled | 65.737% | +1.713 | 69.890% | +4.15% | home | home | — | current artifact recomputation | complete | — | 2026-07-15T23:30:00+00:00 |
| 2026-07-17 | Seattle Storm @ Indiana Fever | 107-110 | 62.070% | -2.058 | 56.673% | -5.40% | home | home | yes | flat_picks.xlsx:607e61329ed3424c | complete | — | 2026-07-17T23:00:00+00:00 |
| 2026-07-17 | Atlanta Dream @ Toronto Tempo | 111-92 | 46.549% | -1.252 | 43.199% | -3.35% | away | away | yes | flat_picks.xlsx:deb68aa7d25042de | complete | — | 2026-07-17T23:00:00+00:00 |
| 2026-07-17 | Los Angeles Sparks @ Chicago Sky | 82-96 | 50.536% | +0.852 | 52.832% | +2.30% | home | home | yes | flat_picks.xlsx:9d42b77cda3d44a7 | complete | — | 2026-07-17T23:00:00+00:00 |
| 2026-07-17 | Connecticut Sun @ Phoenix Mercury | 96-83 | 63.794% | -0.374 | 62.840% | -0.95% | home | home | no | flat_picks.xlsx:33934bb67697420b | complete | — | 2026-07-18T01:30:00+00:00 |
| 2026-07-18 | New York Liberty @ Indiana Fever | 88-108 | 59.331% | +2.164 | 64.897% | +5.57% | home | home | yes | flat_picks.xlsx:ef7830f025d54963 | complete | — | 2026-07-18T23:30:00+00:00 |
| 2026-07-18 | Portland Fire @ Minnesota Lynx | 93-101 | 74.170% | +0.847 | 75.987% | +1.82% | home | home | yes | flat_picks.xlsx:5c32fca0897f4465 | complete | — | 2026-07-18T23:30:00+00:00 |
| 2026-07-18 | Washington Mystics @ Golden State Valkyries | 69-74 | 74.389% | +0.029 | 74.452% | +0.06% | home | home | yes | flat_picks.xlsx:413a2bc86aab48de | complete | — | 2026-07-18T23:30:00+00:00 |
| 2026-07-19 | Los Angeles Sparks @ Dallas Wings | 82-90 | 75.097% | +1.754 | 78.706% | +3.61% | home | home | yes | flat_picks.xlsx:660d896695654e99 | complete | — | 2026-07-18T23:30:00+00:00 |
| 2026-07-19 | Chicago Sky @ Atlanta Dream | 91-93 | 67.571% | -0.046 | 67.460% | -0.11% | home | home | yes | flat_picks.xlsx:9f52810e7f374e1c | complete | — | 2026-07-19T19:00:00+00:00 |
| 2026-07-19 | Connecticut Sun @ Phoenix Mercury | 63-72 | 56.577% | -0.680 | 54.760% | -1.82% | home | home | yes | flat_picks.xlsx:44eda71121684cc1 | complete | — | 2026-07-19T22:30:00+00:00 |
| 2026-07-20 | Las Vegas Aces @ Toronto Tempo | unsettled | 34.271% | +0.522 | 35.578% | +1.31% | away | away | — | flat_picks.xlsx:e35a217d64aa4265 | complete | — | 2026-07-19T22:30:00+00:00 |
| 2026-07-20 | New York Liberty @ Dallas Wings | unsettled | 67.878% | -0.718 | 66.119% | -1.76% | home | home | — | flat_picks.xlsx:5ddbae8d805944cb | diagnostic_conflict_resolution | Smith, Alanna: official Doubtful / ESPN Out | 2026-07-19T22:30:00+00:00 |
| 2026-07-20 | Washington Mystics @ Golden State Valkyries | unsettled | 75.044% | +0.000 | 75.044% | +0.00% | home | home | — | flat_picks.xlsx:0da50dc88c9649b8 | fail_closed | — | 2026-07-19T22:30:00+00:00 |
| 2026-07-20 | Minnesota Lynx @ Seattle Storm | unsettled | 26.096% | +0.000 | 26.096% | +0.00% | away | away | — | flat_picks.xlsx:2d184773492d4263 | fail_closed | — | 2026-07-19T22:30:00+00:00 |

## Confidence-gate and feature-strength sensitivity

Accuracy is conditional on calls. Strict rows exclude source conflicts; diagnostic rows include the labeled conservative resolution.

| Cohort | Availability scale | Confidence gate | Settled | Calls | Accuracy | Brier |
|---|---:|---:|---:|---:|---:|---:|
| strict | 0.0× | 50% | 142 | 142 | 71.8% | 0.2128 |
| strict | 0.0× | 55% | 142 | 115 | 72.2% | 0.2128 |
| strict | 0.0× | 60% | 142 | 94 | 73.4% | 0.2128 |
| strict | 0.0× | 65% | 142 | 59 | 74.6% | 0.2128 |
| strict | 0.0× | 70% | 142 | 30 | 70.0% | 0.2128 |
| strict | 0.0× | 75% | 142 | 7 | 71.4% | 0.2128 |
| strict | 0.5× | 50% | 142 | 142 | 71.1% | 0.2095 |
| strict | 0.5× | 55% | 142 | 115 | 73.9% | 0.2095 |
| strict | 0.5× | 60% | 142 | 91 | 73.6% | 0.2095 |
| strict | 0.5× | 65% | 142 | 61 | 77.0% | 0.2095 |
| strict | 0.5× | 70% | 142 | 30 | 80.0% | 0.2095 |
| strict | 0.5× | 75% | 142 | 10 | 70.0% | 0.2095 |
| strict | 1.0× | 50% | 142 | 142 | 71.1% | 0.2068 |
| strict | 1.0× | 55% | 142 | 111 | 73.9% | 0.2068 |
| strict | 1.0× | 60% | 142 | 92 | 76.1% | 0.2068 |
| strict | 1.0× | 65% | 142 | 66 | 75.8% | 0.2068 |
| strict | 1.0× | 70% | 142 | 32 | 81.2% | 0.2068 |
| strict | 1.0× | 75% | 142 | 11 | 72.7% | 0.2068 |
| strict | 1.5× | 50% | 142 | 142 | 71.8% | 0.2046 |
| strict | 1.5× | 55% | 142 | 113 | 73.5% | 0.2046 |
| strict | 1.5× | 60% | 142 | 92 | 76.1% | 0.2046 |
| strict | 1.5× | 65% | 142 | 66 | 75.8% | 0.2046 |
| strict | 1.5× | 70% | 142 | 34 | 76.5% | 0.2046 |
| strict | 1.5× | 75% | 142 | 14 | 85.7% | 0.2046 |
| strict | 2.0× | 50% | 142 | 142 | 72.5% | 0.2030 |
| strict | 2.0× | 55% | 142 | 117 | 73.5% | 0.2030 |
| strict | 2.0× | 60% | 142 | 94 | 77.7% | 0.2030 |
| strict | 2.0× | 65% | 142 | 63 | 74.6% | 0.2030 |
| strict | 2.0× | 70% | 142 | 37 | 81.1% | 0.2030 |
| strict | 2.0× | 75% | 142 | 16 | 81.2% | 0.2030 |
| diagnostic_including_conflicts | 0.0× | 50% | 142 | 142 | 71.8% | 0.2128 |
| diagnostic_including_conflicts | 0.0× | 55% | 142 | 115 | 72.2% | 0.2128 |
| diagnostic_including_conflicts | 0.0× | 60% | 142 | 94 | 73.4% | 0.2128 |
| diagnostic_including_conflicts | 0.0× | 65% | 142 | 59 | 74.6% | 0.2128 |
| diagnostic_including_conflicts | 0.0× | 70% | 142 | 30 | 70.0% | 0.2128 |
| diagnostic_including_conflicts | 0.0× | 75% | 142 | 7 | 71.4% | 0.2128 |
| diagnostic_including_conflicts | 0.5× | 50% | 142 | 142 | 71.1% | 0.2095 |
| diagnostic_including_conflicts | 0.5× | 55% | 142 | 115 | 73.9% | 0.2095 |
| diagnostic_including_conflicts | 0.5× | 60% | 142 | 91 | 73.6% | 0.2095 |
| diagnostic_including_conflicts | 0.5× | 65% | 142 | 61 | 77.0% | 0.2095 |
| diagnostic_including_conflicts | 0.5× | 70% | 142 | 30 | 80.0% | 0.2095 |
| diagnostic_including_conflicts | 0.5× | 75% | 142 | 10 | 70.0% | 0.2095 |
| diagnostic_including_conflicts | 1.0× | 50% | 142 | 142 | 71.1% | 0.2068 |
| diagnostic_including_conflicts | 1.0× | 55% | 142 | 111 | 73.9% | 0.2068 |
| diagnostic_including_conflicts | 1.0× | 60% | 142 | 92 | 76.1% | 0.2068 |
| diagnostic_including_conflicts | 1.0× | 65% | 142 | 66 | 75.8% | 0.2068 |
| diagnostic_including_conflicts | 1.0× | 70% | 142 | 32 | 81.2% | 0.2068 |
| diagnostic_including_conflicts | 1.0× | 75% | 142 | 11 | 72.7% | 0.2068 |
| diagnostic_including_conflicts | 1.5× | 50% | 142 | 142 | 71.8% | 0.2046 |
| diagnostic_including_conflicts | 1.5× | 55% | 142 | 113 | 73.5% | 0.2046 |
| diagnostic_including_conflicts | 1.5× | 60% | 142 | 92 | 76.1% | 0.2046 |
| diagnostic_including_conflicts | 1.5× | 65% | 142 | 66 | 75.8% | 0.2046 |
| diagnostic_including_conflicts | 1.5× | 70% | 142 | 34 | 76.5% | 0.2046 |
| diagnostic_including_conflicts | 1.5× | 75% | 142 | 14 | 85.7% | 0.2046 |
| diagnostic_including_conflicts | 2.0× | 50% | 142 | 142 | 72.5% | 0.2030 |
| diagnostic_including_conflicts | 2.0× | 55% | 142 | 117 | 73.5% | 0.2030 |
| diagnostic_including_conflicts | 2.0× | 60% | 142 | 94 | 77.7% | 0.2030 |
| diagnostic_including_conflicts | 2.0× | 65% | 142 | 63 | 74.6% | 0.2030 |
| diagnostic_including_conflicts | 2.0× | 70% | 142 | 37 | 81.1% | 0.2030 |
| diagnostic_including_conflicts | 2.0× | 75% | 142 | 16 | 81.2% | 0.2030 |

## Paired uncertainty and selection changes

- Mean Brier delta (availability minus v3): **-0.005986**; paired bootstrap 95% interval **[-0.010762, -0.001186]**.
- Mean winner-accuracy delta: **-0.704%**; paired bootstrap 95% interval **[-4.225%, +2.817%]**.
- The 1.0× feature flipped **7** selections: **3** corrections and **4** newly wrong picks.
- The bootstrap treats games as independent and therefore understates uncertainty from repeated teams, shared injuries, and temporal clustering. It is a diagnostic interval, not promotion evidence.

### Pre-audit versus original-audit cohorts

The feature was first examined on July 17–20. The earlier cohort is therefore the cleaner check against simply fitting the original ten-game observation.

| Cohort | Games | v3 accuracy | + availability accuracy | v3 Brier | + availability Brier | Brier Δ | 95% paired interval |
|---|---:|---:|---:|---:|---:|---:|---|
| Pre-original-audit (through July 16) | 132 | 70.5% | 69.7% | 0.21629 | 0.21039 | -0.00590 | [-0.01096, -0.00089] |
| Original audit window (July 17 onward) | 10 | 90.0% | 90.0% | 0.16656 | 0.15938 | -0.00719 | [-0.02074, +0.00757] |

## Dallas / Paige Bueckers audit

**The status bug is fixed; the Dallas edge is not.** ESPN identifies Paige Bueckers as Out even though the official WNBA PDF omits her. Isolated, her absence moves Dallas from 67.878% to 59.313%. After all listed absences are combined, Dallas remains 66.100% because New York is also missing material players. That is still a Dallas pick, and the Alanna Smith status conflict makes the production result a no-call.

| Date | Merged Bueckers status | Dallas baseline | Paige-only Dallas P | Net availability gap | Net adjusted Dallas P | Production disposition | Outcome |
|---|---|---:|---:|---:|---:|---|---|
| 2026-05-14 | Not listed (treated Available) | 42.562% | 42.562% | -0.058 | 42.409% | eligible on availability inputs | Dallas loss |
| 2026-05-18 | Not listed (treated Available) | 61.741% | 61.741% | +0.000 | 61.741% | NO CALL: incomplete inputs | Dallas win |
| 2026-05-28 | Not listed (treated Available) | 51.600% | 51.600% | +0.381 | 52.628% | eligible on availability inputs | Dallas win |
| 2026-06-01 | Not listed (treated Available) | 68.435% | 68.435% | -0.925 | 66.179% | eligible on availability inputs | Dallas win |
| 2026-06-11 | Not listed (treated Available) | 65.984% | 65.984% | -0.246 | 65.372% | eligible on availability inputs | Dallas win |
| 2026-06-15 | Not listed (treated Available) | 50.687% | 50.687% | +0.000 | 50.687% | NO CALL: incomplete inputs | Dallas win |
| 2026-06-20 | Not listed (treated Available) | 78.502% | 78.502% | +0.605 | 79.678% | eligible on availability inputs | Dallas win |
| 2026-06-28 | Not listed (treated Available) | 41.764% | 41.764% | -0.924 | 39.340% | eligible on availability inputs | Dallas loss |
| 2026-07-12 | Not listed (treated Available) | 77.310% | 77.310% | +0.106 | 77.525% | eligible on availability inputs | Dallas win |
| 2026-07-16 | Not listed (treated Available) | 65.737% | 65.737% | +1.713 | 69.890% | eligible on availability inputs | unsettled |
| 2026-07-19 | Not listed (treated Available) | 75.097% | 75.097% | +1.754 | 78.706% | eligible on availability inputs | Dallas win |
| 2026-07-20 | Out | 67.878% | 59.409% | -0.718 | 66.119% | NO CALL: explicit source conflict | unsettled |

## Largest player-level adjustments

These are challenger inputs, not causal player values. A negative expected-loss number means the noisy proxy rated the named player below the team replacement prior.

| Date | Matchup | Team | Player | Status | Proj min | Impact above repl /100 | Expected points lost |
|---|---|---|---|---|---:|---:|---:|
| 2026-06-30 | Las Vegas Aces @ New York Liberty | Las Vegas Aces | A'ja Wilson | Out | 32.0 | +7.47 | +4.787 |
| 2026-07-03 | Chicago Sky @ Las Vegas Aces | Las Vegas Aces | A'ja Wilson | Out | 30.8 | +5.80 | +3.576 |
| 2026-07-20 | New York Liberty @ Dallas Wings | Dallas Wings | Paige Bueckers | Out | 32.3 | +5.17 | +3.343 |
| 2026-07-05 | Indiana Fever @ Las Vegas Aces | Las Vegas Aces | A'ja Wilson | Out | 28.8 | +5.58 | +3.211 |
| 2026-06-25 | New York Liberty @ Seattle Storm | New York Liberty | Breanna Stewart | Out | 30.4 | +4.90 | +2.980 |
| 2026-07-17 | Seattle Storm @ Indiana Fever | Indiana Fever | Aliyah Boston | Out | 25.7 | +5.45 | +2.796 |
| 2026-06-25 | Los Angeles Sparks @ Toronto Tempo | Toronto Tempo | Kiki Rice | Out | 19.1 | +6.09 | +2.325 |
| 2026-07-19 | Los Angeles Sparks @ Dallas Wings | Los Angeles Sparks | Kelsey Plum | Out | 25.8 | +4.21 | +2.170 |
| 2026-07-06 | Seattle Storm @ Los Angeles Sparks | Los Angeles Sparks | Kelsey Plum | Out | 26.0 | +4.07 | +2.111 |
| 2026-06-02 | Las Vegas Aces @ Los Angeles Sparks | Los Angeles Sparks | Kelsey Plum | Out | 24.6 | +4.25 | +2.086 |
| 2026-07-20 | New York Liberty @ Dallas Wings | New York Liberty | Leonie Fiebich | Out | 24.3 | +4.21 | +2.047 |
| 2026-07-10 | Chicago Sky @ Los Angeles Sparks | Los Angeles Sparks | Kelsey Plum | Out | 23.9 | +4.09 | +1.958 |
| 2026-06-13 | Dallas Wings @ Portland Fire | Dallas Wings | Paige Bueckers | Out | 27.9 | +3.45 | +1.929 |
| 2026-06-12 | Toronto Tempo @ Washington Mystics | Toronto Tempo | Kiki Rice | Out | 20.3 | +4.66 | +1.895 |
| 2026-07-12 | New York Liberty @ Toronto Tempo | New York Liberty | Leonie Fiebich | Out | 27.0 | +3.44 | +1.861 |
| 2026-06-22 | Toronto Tempo @ Atlanta Dream | Toronto Tempo | Kiki Rice | Out | 19.2 | +4.76 | +1.824 |
| 2026-07-06 | Connecticut Sun @ Minnesota Lynx | Minnesota Lynx | Olivia Miles | Out | 28.6 | +3.08 | +1.763 |
| 2026-06-26 | Portland Fire @ Chicago Sky | Chicago Sky | Aicha Coulibaly | Out | 12.2 | +6.99 | +1.705 |
| 2026-06-19 | Toronto Tempo @ Connecticut Sun | Toronto Tempo | Nyara Sabally | Out | 19.8 | +4.22 | +1.669 |
| 2026-06-01 | Seattle Storm @ Dallas Wings | Dallas Wings | Awak Kuier | Out | 14.1 | +5.86 | +1.656 |
| 2026-06-19 | Toronto Tempo @ Connecticut Sun | Toronto Tempo | Kiki Rice | Out | 19.5 | +4.14 | +1.617 |
| 2026-06-08 | New York Liberty @ Connecticut Sun | Connecticut Sun | Brittney Griner | Out | 20.7 | +3.90 | +1.615 |
| 2026-06-05 | Dallas Wings @ Los Angeles Sparks | Dallas Wings | Awak Kuier | Out | 14.3 | +5.61 | +1.605 |
| 2026-06-09 | Dallas Wings @ Minnesota Lynx | Dallas Wings | Awak Kuier | Out | 17.2 | +4.61 | +1.591 |
| 2026-06-27 | Los Angeles Sparks @ Indiana Fever | Los Angeles Sparks | Kelsey Plum | Out | 26.0 | +3.06 | +1.591 |
| 2026-07-08 | Golden State Valkyries @ Toronto Tempo | Toronto Tempo | Brittney Sykes | Out | 22.3 | +3.56 | +1.588 |
| 2026-06-16 | Toronto Tempo @ Indiana Fever | Toronto Tempo | Kiki Rice | Out | 19.6 | +4.00 | +1.570 |
| 2026-06-07 | Chicago Sky @ Toronto Tempo | Toronto Tempo | Kiki Rice | Out | 22.7 | +3.45 | +1.567 |
| 2026-07-08 | Minnesota Lynx @ Connecticut Sun | Minnesota Lynx | Olivia Miles | Out | 25.7 | +3.04 | +1.560 |
| 2026-06-27 | Phoenix Mercury @ Toronto Tempo | Toronto Tempo | Kiki Rice | Out | 18.4 | +4.09 | +1.502 |
| 2026-06-27 | Los Angeles Sparks @ Indiana Fever | Indiana Fever | Caitlin Clark | Out | 26.0 | +2.89 | +1.502 |
| 2026-06-30 | Las Vegas Aces @ New York Liberty | New York Liberty | Satou Sabally | Out | 14.6 | +5.13 | +1.502 |
| 2026-07-06 | Golden State Valkyries @ Washington Mystics | Washington Mystics | Sonia Citron | Out | 31.6 | +2.34 | +1.480 |
| 2026-06-17 | Minnesota Lynx @ Los Angeles Sparks | Los Angeles Sparks | Kelsey Plum | Out | 30.8 | +2.39 | +1.473 |
| 2026-06-25 | New York Liberty @ Seattle Storm | New York Liberty | Satou Sabally | Out | 15.2 | +4.71 | +1.433 |
| 2026-06-10 | Connecticut Sun @ Toronto Tempo | Toronto Tempo | Kiki Rice | Out | 22.4 | +3.17 | +1.424 |
| 2026-06-08 | Seattle Storm @ Las Vegas Aces | Las Vegas Aces | Chennedy Carter | Out | 15.1 | +4.63 | +1.398 |
| 2026-07-11 | New York Liberty @ Minnesota Lynx | New York Liberty | Satou Sabally | Out | 14.1 | +4.95 | +1.393 |
| 2026-06-28 | New York Liberty @ Golden State Valkyries | New York Liberty | Satou Sabally | Out | 14.4 | +4.78 | +1.373 |
| 2026-05-30 | Los Angeles Sparks @ Connecticut Sun | Los Angeles Sparks | Kelsey Plum | Out | 19.8 | +3.44 | +1.360 |

## What this test cannot establish

1. 142 retrospectively reconstructed games cannot replace a prospectively observed validation cohort.
2. The report PDFs were recovered after the games, so their embedded publication times are useful diagnostics but not equivalent to prospectively observed snapshots.
3. The impact prior is heavily shrunk raw box plus/minus, not WNBA RAPM or lineup-adjusted causal impact.
4. Current rosters were queried during reconstruction; transactions effective between the game and retrieval can create entity risk.
5. Coverage is incomplete: only 142 of 164 settled games are strictly conflict-free and feature-complete.

## Keep/remove recommendation

Keep the official report parser, immutable snapshots, player mapping, projected-minute contract, and fail-closed model hook. Do **not** replace the active WNBA artifact or assign a production coefficient from these 142 diagnostic games. Run the collector prospectively, replace shrunk raw plus/minus with a WNBA-specific regularized lineup-impact prior, then re-run a preregistered fresh cohort.
