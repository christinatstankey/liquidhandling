# SDS Identity Audit - 2026-05-13

Scope: all 476 canonical `data/reagents/*.json` records. For records with a local `data/sds-pdfs/<CAS>.pdf`, Section 1 was parsed with the repo SDS parser (`pdfplumber`) and compared to JSON `cas` and `name`.

## Summary

- Total reagent JSON records: 476
- Local downloaded SDS PDFs matched by filename CAS: 146
- Direct Section 1 CAS + exact name matches: 130
- Product/mixture SDSs with no top-level CAS, but target CAS found in SDS text: 5
- Product/mixture SDSs with matching product name but JSON CAS not found in extracted SDS text: 11
- Records with no local SDS PDF available for this audit: 330
- Name mismatches among local SDS PDFs: 0
- Hard identity mismatches among local SDS PDFs: 0

## CAS Not Corroborated By SDS Text

These local SDS PDFs have an exact product-name match, but Sigma does not expose the JSON CAS as the top-level Section 1 CAS or anywhere else in extracted text. They need manual source review before treating the CAS as SDS-confirmed.

|json_cas|json_name|sds_name_section1|sds_product_number|sds_revision_date|notes|
|---|---|---|---|---|---|
|1049724-28-8|(+)-Etomoxir sodium salt hydrate|(+)-Etomoxir sodium salt hydrate|E1905|11/06/2025|SDS Section 1 has no top-level CAS and JSON CAS was not found elsewhere in extracted SDS text|
|1049740-55-7|H-89 dihydrochloride hydrate|H-89 dihydrochloride hydrate|B1427|04/24/2025|SDS Section 1 has no top-level CAS and JSON CAS was not found elsewhere in extracted SDS text|
|1049747-17-2|(S)-3,5-Dihydroxyphenylglycine hydrate|(S)-3,5-Dihydroxyphenylglycine hydrate|D3689|09/07/2024|SDS Section 1 has no top-level CAS and JSON CAS was not found elsewhere in extracted SDS text|
|1173097-76-1|U0126 ethanolate|U0126 ethanolate|U120|08/25/2025|SDS Section 1 has no top-level CAS and JSON CAS was not found elsewhere in extracted SDS text|
|1217468-11-5|Hygromycin B solution, from Streptomyces|Hygromycin B solution, from Streptomyces|H0654|09/06/2024|SDS Section 1 has no top-level CAS and JSON CAS was not found elsewhere in extracted SDS text|
|136112-00-0|Phenol – chloroform – isoamyl alcohol|Phenol – chloroform – isoamyl alcohol|77619|12/01/2025|SDS Section 1 has no top-level CAS and JSON CAS was not found elsewhere in extracted SDS text|
|139-33-3|Ethylenediaminetetraacetic acid disodium salt|Ethylenediaminetetraacetic acid disodium salt|E7889|09/07/2024|SDS Section 1 has no top-level CAS and JSON CAS was not found elsewhere in extracted SDS text|
|28718-91-4|DAPI, dilactate|DAPI, dilactate|D9564|05/12/2025|SDS Section 1 has no top-level CAS and JSON CAS was not found elsewhere in extracted SDS text|
|29701-07-3|Kanamycin B sulfate salt|Kanamycin B sulfate salt|B5264|04/24/2025|SDS Section 1 has no top-level CAS and JSON CAS was not found elsewhere in extracted SDS text|
|485-49-4|EX-CELL(R) 325 PF CHO|EX-CELL(R) 325 PF CHO|14340|09/03/2024|SDS Section 1 has no top-level CAS and JSON CAS was not found elsewhere in extracted SDS text|
|6055-72-7|Adenine hydrochloride hydrate|Adenine hydrochloride hydrate|A9795|04/24/2025|SDS Section 1 has no top-level CAS and JSON CAS was not found elsewhere in extracted SDS text|

## Product SDS: CAS Found In Ingredients/Text

These have no top-level Section 1 CAS, but the target CAS appears in the SDS text, usually Section 3 composition. The product name matches.

|json_cas|json_name|sds_product_number|sds_revision_date|cas_match_type|notes|
|---|---|---|---|---|---|
|110-26-9|N,N′-Methylenebisacrylamide solution|M1533|11/06/2025|section3_component|SDS Section 1 has no top-level CAS; JSON CAS appears in Section 3 ingredients|
|629-11-8|1,6-Hexanediol solution|88571|05/12/2025|elsewhere_in_sds|SDS Section 1 has no top-level CAS; JSON CAS appears elsewhere in SDS|
|72-57-1|Trypan Blue solution|T8154|02/02/2026|section3_component|SDS Section 1 has no top-level CAS; JSON CAS appears in Section 3 ingredients|
|7664-38-2|Phosphoric acid|695017|11/06/2025|section3_component|SDS Section 1 has no top-level CAS; JSON CAS appears in Section 3 ingredients|
|85-01-8|Phenanthrene solution|31304|02/07/2023|elsewhere_in_sds|SDS Section 1 has no top-level CAS; JSON CAS appears elsewhere in SDS|

## Missing Local SDS PDFs

The following records could not be checked against a downloaded SDS because `data/sds-pdfs/<CAS>.pdf` is absent.

|json_cas|json_name|file|
|---|---|---|
|10049-21-5|Sodium phosphate monobasic monohydrate|data/reagents/10049-21-5.json|
|102-71-6|Triethanolamine|data/reagents/102-71-6.json|
|102396-24-7|Jasplakinolide|data/reagents/102396-24-7.json|
|102783-51-7|2′-Deoxycytidine 5′-triphosphate disodium|data/reagents/102783-51-7.json|
|103476-89-7|Leupeptin hemisulfate salt|data/reagents/103476-89-7.json|
|104821-25-2|Dihydroethidium|data/reagents/104821-25-2.json|
|10510-54-0|Cresyl Violet acetate|data/reagents/10510-54-0.json|
|10540-29-1|Tamoxifen|data/reagents/10540-29-1.json|
|1062368-62-0|LDN193189 hydrochloride|data/reagents/1062368-62-0.json|
|107-35-7|Taurine|data/reagents/107-35-7.json|
|107-43-7|Betaine|data/reagents/107-43-7.json|
|107-92-6|Butyric acid|data/reagents/107-92-6.json|
|108-24-7|Acetic anhydride|data/reagents/108-24-7.json|
|108-45-2|m-Phenylenediamine|data/reagents/108-45-2.json|
|110-15-6|Succinic acid|data/reagents/110-15-6.json|
|110-54-3|Hexane|data/reagents/110-54-3.json|
|110-86-1|Pyridine|data/reagents/110-86-1.json|
|110-89-4|Piperidine|data/reagents/110-89-4.json|
|11024-24-1|Digitonin|data/reagents/11024-24-1.json|
|111-30-8|Glutaraldehyde solution|data/reagents/111-30-8.json|
|112-80-1|Oleic acid|data/reagents/112-80-1.json|
|1132-61-2|MOPS|data/reagents/1132-61-2.json|
|1134-47-0|(±)-Baclofen|data/reagents/1134-47-0.json|
|114-07-8|Erythromycin|data/reagents/114-07-8.json|
|114977-28-5|Docetaxel|data/reagents/114977-28-5.json|
|115-39-9|Bromophenol Blue|data/reagents/115-39-9.json|
|115532-52-0|Tetramethylrhodamine ethyl ester perchlorate|data/reagents/115532-52-0.json|
|117570-53-3|DMXAA|data/reagents/117570-53-3.json|
|118-00-3|Guanosine|data/reagents/118-00-3.json|
|1185-53-1|Trizma® hydrochloride solution|data/reagents/1185-53-1.json|
|120-51-4|Benzyl benzoate|data/reagents/120-51-4.json|
|1202867-00-2|Dynasore hydrate|data/reagents/1202867-00-2.json|
|1206711-16-1|DMH1|data/reagents/1206711-16-1.json|
|122320-73-4|Rosiglitazone|data/reagents/122320-73-4.json|
|1232410-49-9|VE-821|data/reagents/1232410-49-9.json|
|1239-45-8|Ethidium bromide solution|data/reagents/1239-45-8.json|
|124-20-9|Spermidine|data/reagents/124-20-9.json|
|126150-97-8|BAPTA-AM|data/reagents/126150-97-8.json|
|1266615-59-1|MES hydrate|data/reagents/1266615-59-1.json|
|1268524-70-4|(+)-JQ1|data/reagents/1268524-70-4.json|
|127-07-1|Hydroxyurea|data/reagents/127-07-1.json|
|128-53-0|N-Ethylmaleimide|data/reagents/128-53-0.json|
|129-56-6|SP600125|data/reagents/129-56-6.json|
|129830-38-2|Y-27632 dihydrochloride|data/reagents/129830-38-2.json|
|1310-58-3|POTASSIUM HYDROXIDE PELLET|data/reagents/1310-58-3.json|
|1310-73-2|Sodium hydroxide|data/reagents/1310-73-2.json|
|131740-09-5|Flavopiridol hydrochloride hydrate|data/reagents/131740-09-5.json|
|1320-06-5|Oil Red O|data/reagents/1320-06-5.json|
|13292-46-1|Rifampicin|data/reagents/13292-46-1.json|
|133407-82-6|Z-Leu-Leu-Leu-al|data/reagents/133407-82-6.json|
|134381-21-8|Epoxomicin|data/reagents/134381-21-8.json|
|1346704-33-3|GSK343|data/reagents/1346704-33-3.json|
|13472-35-0|Sodium phosphate monobasic dihydrate|data/reagents/13472-35-0.json|
|13721-39-6|Sodium orthovanadate|data/reagents/13721-39-6.json|
|13746-66-2|Potassium hexacyanoferrate(III)|data/reagents/13746-66-2.json|
|13803-65-1|Anhydrotetracycline hydrochloride|data/reagents/13803-65-1.json|
|13957-31-8|4-Thiouridine|data/reagents/13957-31-8.json|
|1397-89-3|Amphotericin B solution|data/reagents/1397-89-3.json|
|1401-55-4|Tannic acid|data/reagents/1401-55-4.json|
|141-43-5|Ethanolamine|data/reagents/141-43-5.json|
|141-78-6|Ethyl acetate|data/reagents/141-78-6.json|
|1418013-75-8|NMS-873|data/reagents/1418013-75-8.json|
|142-82-5|Heptane|data/reagents/142-82-5.json|
|1421-86-9|Strychnine hydrochloride|data/reagents/1421-86-9.json|
|143-74-8|Phenol Red|data/reagents/143-74-8.json|
|144-48-9|Iodoacetamide|data/reagents/144-48-9.json|
|14639-81-7|Calcium chloride solution ~1 M in H2O|data/reagents/14639-81-7.json|
|14930-96-2|Cytochalasin B, from Drechslera dematioidea|data/reagents/14930-96-2.json|
|15086-94-9|Eosin Y|data/reagents/15086-94-9.json|
|151-21-3|Sodium dodecyl sulfate|data/reagents/151-21-3.json|
|151-50-8|Potassium cyanide|data/reagents/151-50-8.json|
|1510-21-0|Cholesteryl hemisuccinate|data/reagents/1510-21-0.json|
|152-11-4|(±)-Verapamil hydrochloride|data/reagents/152-11-4.json|
|152121-30-7|SB 202190|data/reagents/152121-30-7.json|
|154-17-6|2-Deoxy-D-glucose|data/reagents/154-17-6.json|
|154804-51-0|β-Glycerophosphate disodium salt hydrate|data/reagents/154804-51-0.json|
|1597403-47-8|ISRIB|data/reagents/1597403-47-8.json|
|16052-06-5|EPPS|data/reagents/16052-06-5.json|
|16393-49-0|Ammonium hydroxide solution|data/reagents/16393-49-0.json|
|16561-29-8|Phorbol 12-myristate 13-acetate|data/reagents/16561-29-8.json|
|16595-80-5|Levamisole hydrochloride|data/reagents/16595-80-5.json|
|1670-14-0|Benzamidine hydrochloride|data/reagents/1670-14-0.json|
|16941-32-5|Glucagon|data/reagents/16941-32-5.json|
|16980-89-5|N6,2′-O-Dibutyryladenosine 3′,5′-cyclic|data/reagents/16980-89-5.json|
|1713265-25-8|L-Ascorbic acid 2-phosphate|data/reagents/1713265-25-8.json|
|186692-46-6|Roscovitine|data/reagents/186692-46-6.json|
|18883-66-4|Streptozocin|data/reagents/18883-66-4.json|
|19333-65-4|Phosphocreatine disodium salt hydrate|data/reagents/19333-65-4.json|
|19545-26-7|Wortmannin|data/reagents/19545-26-7.json|
|19817-92-6|Uridine 5′-triphosphate sodium|data/reagents/19817-92-6.json|
|206752-36-5|Benzamidine hydrochloride hydrate|data/reagents/206752-36-5.json|
|2140-46-7|25-Hydroxycholesterol|data/reagents/2140-46-7.json|
|218137-86-1|C75|data/reagents/218137-86-1.json|
|21829-25-4|Nifedipine|data/reagents/21829-25-4.json|
|22144-77-0|Cytochalasin D|data/reagents/22144-77-0.json|
|22189-32-8|Spectinomycin dihydrochloride pentahydrate|data/reagents/22189-32-8.json|
|22862-76-6|Anisomycin, from Streptomyces griseolus|data/reagents/22862-76-6.json|
|2353-45-9|Fast Green FCF|data/reagents/2353-45-9.json|
|2379-57-9|DNQX|data/reagents/2379-57-9.json|
|2390-54-7|Thioflavin T|data/reagents/2390-54-7.json|
|24280-93-1|Mycophenolic acid|data/reagents/24280-93-1.json|
|2497-59-8|Triton™ X-100|data/reagents/2497-59-8.json|
|252917-06-9|CHIR99021|data/reagents/252917-06-9.json|
|25316-40-9|Doxorubicin hydrochloride|data/reagents/25316-40-9.json|
|25389-94-0|Kanamycin sulfate, from Streptomyces|data/reagents/25389-94-0.json|
|254753-54-3|Monastrol|data/reagents/254753-54-3.json|
|26305-03-3|Pepstatin A|data/reagents/26305-03-3.json|
|26833-87-4|Omacetaxine mepesuccinate|data/reagents/26833-87-4.json|
|27565-41-9|DL-Dithiothreitol solution|data/reagents/27565-41-9.json|
|2763-96-4|Muscimol|data/reagents/2763-96-4.json|
|284028-89-3|XAV939|data/reagents/284028-89-3.json|
|28643-80-3|Nigericin sodium salt|data/reagents/28643-80-3.json|
|28718-90-3|DAPI|data/reagents/28718-90-3.json|
|288-32-4|Imidazole|data/reagents/288-32-4.json|
|28822-58-4|3-Isobutyl-1-methylxanthine|data/reagents/28822-58-4.json|
|298-83-9|Nitro Blue Tetrazolium|data/reagents/298-83-9.json|
|298-93-1|Thiazolyl Blue Tetrazolium Bromide|data/reagents/298-93-1.json|
|29836-26-8|Octyl β-D-glucopyranoside|data/reagents/29836-26-8.json|
|299-27-4|Potassium D-gluconate|data/reagents/299-27-4.json|
|299953-00-7|Mirin|data/reagents/299953-00-7.json|
|302-79-4|Retinoic acid|data/reagents/302-79-4.json|
|302-95-4|Sodium deoxycholate|data/reagents/302-95-4.json|
|30827-99-7|4-(2-Aminoethyl)benzenesulfonyl fluoride|data/reagents/30827-99-7.json|
|314045-39-1|BPTES|data/reagents/314045-39-1.json|
|31430-18-9|Nocodazole|data/reagents/31430-18-9.json|
|320-67-2|5-Azacytidine|data/reagents/320-67-2.json|
|329-98-6|Phenylmethanesulfonyl fluoride|data/reagents/329-98-6.json|
|33069-62-4|Paclitaxel|data/reagents/33069-62-4.json|
|333-93-7|Putrescine dihydrochloride|data/reagents/333-93-7.json|
|334-50-9|Spermidine trihydrochloride|data/reagents/334-50-9.json|
|33419-42-0|Etoposide|data/reagents/33419-42-0.json|
|338967-87-6|Mdivi-1|data/reagents/338967-87-6.json|
|34233-69-7|Clozapine N-oxide|data/reagents/34233-69-7.json|
|34369-07-8|Adenosine 5′-triphosphate disodium salt|data/reagents/34369-07-8.json|
|35891-70-4|Myriocin From Mycelia Sterilia|data/reagents/35891-70-4.json|
|36051-68-0|Cytidine 5′-triphosphate disodium salt|data/reagents/36051-68-0.json|
|363-24-6|Prostaglandin E2|data/reagents/363-24-6.json|
|364-98-7|Diazoxide|data/reagents/364-98-7.json|
|367-93-1|Isopropyl β-D-thiogalactopyranoside solution|data/reagents/367-93-1.json|
|370-86-5|Carbonyl cyanide 4-|data/reagents/370-86-5.json|
|38966-21-1|Aphidicolin, from Nigrospora sphaerica|data/reagents/38966-21-1.json|
|391210-10-9|PD 0325901|data/reagents/391210-10-9.json|
|404-86-4|Capsaicin|data/reagents/404-86-4.json|
|40709-69-1|1(S),9(R)-(−)-Bicuculline methiodide|data/reagents/40709-69-1.json|
|4091-99-0|2',7'-Dichlorodihydrofluorescein diacetate|data/reagents/4091-99-0.json|
|4197-25-5|Sudan Black B|data/reagents/4197-25-5.json|
|4265-07-0|Phospho(enol)pyruvic acid monopotassium|data/reagents/4265-07-0.json|
|4311-88-0|Necrostatin-1|data/reagents/4311-88-0.json|
|443-48-1|Metronidazole|data/reagents/443-48-1.json|
|4800-94-6|Carbenicillin disodium salt|data/reagents/4800-94-6.json|
|487-60-5|Indoxyl β-D-glucoside|data/reagents/487-60-5.json|
|492-27-3|Kynurenic acid|data/reagents/492-27-3.json|
|50-00-0|Formaldehyde solution, 36.5-38%|data/reagents/50-00-0.json|
|50-01-1|Guanidine hydrochloride|data/reagents/50-01-1.json|
|50-02-2|Dexamethasone|data/reagents/50-02-2.json|
|50-07-7|Mitomycin C, from Streptomyces caespitosus|data/reagents/50-07-7.json|
|50-23-7|Hydrocortisone|data/reagents/50-23-7.json|
|50-28-2|β-Estradiol|data/reagents/50-28-2.json|
|50-76-0|Dactinomycin|data/reagents/50-76-0.json|
|50-81-7|L-Ascorbic acid|data/reagents/50-81-7.json|
|50-89-5|Thymidine|data/reagents/50-89-5.json|
|50-90-8|5-Chloro-2′-deoxyuridine|data/reagents/50-90-8.json|
|50-91-9|5-Fluoro-2′-deoxyuridine|data/reagents/50-91-9.json|
|50-99-7|D-(+)-Glucose|data/reagents/50-99-7.json|
|51-21-8|5-Fluorouracil|data/reagents/51-21-8.json|
|51-41-2|Norepinephrine|data/reagents/51-41-2.json|
|51-45-6|Histamine|data/reagents/51-45-6.json|
|51-55-8|Atropine|data/reagents/51-55-8.json|
|51-79-6|Urethane|data/reagents/51-79-6.json|
|5142-23-4|3-Methyladenine|data/reagents/5142-23-4.json|
|517-28-2|Hematoxylin|data/reagents/517-28-2.json|
|51805-45-9|Tris(2-carboxyethyl)phosphine hydrochloride|data/reagents/51805-45-9.json|
|52-90-4|L-Cysteine|data/reagents/52-90-4.json|
|53-84-9|β-Nicotinamide adenine dinucleotide hydrate|data/reagents/53-84-9.json|
|53-85-0|5,6-Dichlorobenzimidazole 1-β-D-|data/reagents/53-85-0.json|
|53-86-1|Indomethacin|data/reagents/53-86-1.json|
|53123-88-9|Rapamycin, from Streptomyces|data/reagents/53123-88-9.json|
|5328-37-0|L-(+)-Arabinose|data/reagents/5328-37-0.json|
|533-48-2|d-Desthiobiotin|data/reagents/533-48-2.json|
|54-42-2|5-Iodo-2′-deoxyuridine|data/reagents/54-42-2.json|
|54-71-7|Pilocarpine hydrochloride|data/reagents/54-71-7.json|
|541-15-1|L-Carnitine|data/reagents/541-15-1.json|
|548-62-9|Crystal Violet|data/reagents/548-62-9.json|
|55-06-1|3,3′,5-Triiodo-L-thyronine sodium salt|data/reagents/55-06-1.json|
|55-98-1|Busulfan|data/reagents/55-98-1.json|
|555-60-2|Carbonyl cyanide 3-chlorophenylhydrazone|data/reagents/555-60-2.json|
|56-40-6|Glycine|data/reagents/56-40-6.json|
|56-41-7|L-Alanine|data/reagents/56-41-7.json|
|56-45-1|L-Serine|data/reagents/56-45-1.json|
|56-75-7|Chloramphenicol|data/reagents/56-75-7.json|
|56-81-5|Glycerol|data/reagents/56-81-5.json|
|56-84-8|L-Aspartic acid|data/reagents/56-84-8.json|
|56-85-9|L-Glutamine|data/reagents/56-85-9.json|
|56-86-0|L-Glutamic acid|data/reagents/56-86-0.json|
|56-87-1|L-Lysine|data/reagents/56-87-1.json|
|56-92-8|Histamine dihydrochloride|data/reagents/56-92-8.json|
|56092-82-1|Ionomycin calcium salt, from Streptomyces|data/reagents/56092-82-1.json|
|5625-37-6|PIPES|data/reagents/5625-37-6.json|
|56396-35-1|UK-5099|data/reagents/56396-35-1.json|
|57-10-3|Palmitic acid|data/reagents/57-10-3.json|
|57-13-6|Urea|data/reagents/57-13-6.json|
|57-24-9|Strychnine|data/reagents/57-24-9.json|
|57-50-1|Sucrose|data/reagents/57-50-1.json|
|57-55-6|1,2-Propanediol|data/reagents/57-55-6.json|
|57-66-9|Probenecid|data/reagents/57-66-9.json|
|57-83-0|Progesterone|data/reagents/57-83-0.json|
|57-88-5|Cholesterol|data/reagents/57-88-5.json|
|571203-78-6|Erastin|data/reagents/571203-78-6.json|
|576-19-2|Biocytin|data/reagents/576-19-2.json|
|58-08-2|Caffeine|data/reagents/58-08-2.json|
|58-58-2|Puromycin dihydrochloride, from|data/reagents/58-58-2.json|
|58-61-7|Adenosine|data/reagents/58-61-7.json|
|58-63-9|Inosine|data/reagents/58-63-9.json|
|58-85-5|Biotin|data/reagents/58-85-5.json|
|58-96-8|Uridine|data/reagents/58-96-8.json|
|58880-19-6|Trichostatin A|data/reagents/58880-19-6.json|
|59-14-3|5-Bromo-2′-deoxyuridine|data/reagents/59-14-3.json|
|59-23-4|D-(+)-Galactose|data/reagents/59-23-4.json|
|59721-29-8|Camostat mesylate|data/reagents/59721-29-8.json|
|5984-95-2|(−)-Isoproterenol hydrochloride|data/reagents/5984-95-2.json|
|59865-13-3|Cyclosporin A|data/reagents/59865-13-3.json|
|60-00-4|Ethylenediaminetetraacetic acid|data/reagents/60-00-4.json|
|60-24-2|2-Mercaptoethanol|data/reagents/60-24-2.json|
|60-29-7|Diethyl ether|data/reagents/60-29-7.json|
|60-33-3|Linoleic acid|data/reagents/60-33-3.json|
|6055-19-2|Cyclophosphamide monohydrate|data/reagents/6055-19-2.json|
|606-68-8|β-Nicotinamide adenine dinucleotide, reduced|data/reagents/606-68-8.json|
|6131-99-3|Sodium cacodylate trihydrate|data/reagents/6131-99-3.json|
|6132-04-3|Sodium citrate dihydrate|data/reagents/6132-04-3.json|
|6138-23-4|D-(+)-Trehalose dihydrate|data/reagents/6138-23-4.json|
|616-91-1|N-Acetyl-L-cysteine|data/reagents/616-91-1.json|
|62-56-6|Thiourea|data/reagents/62-56-6.json|
|62758-13-8|Resazurin sodium salt|data/reagents/62758-13-8.json|
|63-68-3|L-Methionine|data/reagents/63-68-3.json|
|63-91-2|L-Phenylalanine|data/reagents/63-91-2.json|
|635-65-4|Bilirubin|data/reagents/635-65-4.json|
|6384-92-5|N-Methyl-D-aspartic acid|data/reagents/6384-92-5.json|
|64-17-5|Ethyl Alcohol, pure|data/reagents/64-17-5.json|
|64-18-6|Formic acid|data/reagents/64-18-6.json|
|64-75-5|Tetracycline hydrochloride|data/reagents/64-75-5.json|
|64-86-8|Colchicine|data/reagents/64-86-8.json|
|65-46-3|Cytidine|data/reagents/65-46-3.json|
|6505-45-9|Indole-3-acetic acid sodium salt|data/reagents/6505-45-9.json|
|656820-32-5|Reversine|data/reagents/656820-32-5.json|
|657-27-2|L-Lysine monohydrochloride|data/reagents/657-27-2.json|
|66-22-8|Uracil|data/reagents/66-22-8.json|
|66-81-9|Cycloheximide|data/reagents/66-81-9.json|
|66108-95-0|Histodenz™|data/reagents/66108-95-0.json|
|66575-29-9|Forskolin|data/reagents/66575-29-9.json|
|667463-62-9|BIO|data/reagents/667463-62-9.json|
|67-42-5|Ethylene glycol-bis(2-aminoethylether)-|data/reagents/67-42-5.json|
|67-63-0|2-Propanol|data/reagents/67-63-0.json|
|67-64-1|Acetone|data/reagents/67-64-1.json|
|67-66-3|Chloroform|data/reagents/67-66-3.json|
|67-68-5|Dimethyl sulfoxide|data/reagents/67-68-5.json|
|67526-95-8|Thapsigargin|data/reagents/67526-95-8.json|
|675576-97-3|Nutlin-3a|data/reagents/675576-97-3.json|
|68-12-2|N,N-Dimethylformamide|data/reagents/68-12-2.json|
|68-41-7|D-Cycloserine|data/reagents/68-41-7.json|
|68-94-0|Hypoxanthine|data/reagents/68-94-0.json|
|68047-06-3|(Z)-4-Hydroxytamoxifen|data/reagents/68047-06-3.json|
|6893-02-3|3,3′,5-Triiodo-L-thyronine|data/reagents/6893-02-3.json|
|69-53-4|Ampicillin|data/reagents/69-53-4.json|
|69-65-8|D-Mannitol|data/reagents/69-65-8.json|
|69-89-6|Xanthine|data/reagents/69-89-6.json|
|69227-93-6|n-Dodecyl β-D-maltoside|data/reagents/69227-93-6.json|
|6976-37-0|Bis-Tris|data/reagents/6976-37-0.json|
|70-18-8|L-Glutathione reduced|data/reagents/70-18-8.json|
|70-47-3|L-Asparagine|data/reagents/70-47-3.json|
|7083-71-8|Emetine dihydrochloride|data/reagents/7083-71-8.json|
|71-00-1|L-Histidine|data/reagents/71-00-1.json|
|71-44-3|Spermine|data/reagents/71-44-3.json|
|71203-35-5|ML 141|data/reagents/71203-35-5.json|
|71827-03-7|Ivermectin|data/reagents/71827-03-7.json|
|72-18-4|L-Valine|data/reagents/72-18-4.json|
|72-19-5|L-Threonine|data/reagents/72-19-5.json|
|7240-90-6|5-Bromo-4-chloro-3-indolyl β-D-|data/reagents/7240-90-6.json|
|73-22-3|L-Tryptophan|data/reagents/73-22-3.json|
|73-24-5|Adenine|data/reagents/73-24-5.json|
|73-32-5|L-Isoleucine|data/reagents/73-32-5.json|
|73565-55-6|Poly-D-lysine hydrobromide|data/reagents/73565-55-6.json|
|7365-45-9|HEPES|data/reagents/7365-45-9.json|
|738-70-5|Trimethoprim|data/reagents/738-70-5.json|
|74-79-3|L-Arginine|data/reagents/74-79-3.json|
|7447-40-7|Potassium chloride|data/reagents/7447-40-7.json|
|7447-41-8|Lithium chloride|data/reagents/7447-41-8.json|
|75-05-8|Acetonitrile|data/reagents/75-05-8.json|
|75-09-2|Dichloromethane|data/reagents/75-09-2.json|
|75-12-7|Formamide|data/reagents/75-12-7.json|
|75330-75-5|Mevinolin, from Aspergillus sp.|data/reagents/75330-75-5.json|
|76-03-9|Trichloroacetic acid|data/reagents/76-03-9.json|
|76-05-1|Trifluoroacetic acid|data/reagents/76-05-1.json|
|76326-31-3|DL-2-Amino-5-phosphonopentanoic acid|data/reagents/76326-31-3.json|
|76343-93-6|Latrunculin A|data/reagents/76343-93-6.json|
|76343-94-7|Latrunculin B, from Latruncula magnifica|data/reagents/76343-94-7.json|
|7646-85-7|Zinc chloride|data/reagents/7646-85-7.json|
|7647-01-0|Hydrochloric acid|data/reagents/7647-01-0.json|
|7689-03-4|(S)-(+)-Camptothecin|data/reagents/7689-03-4.json|
|7722-84-1|Hydrogen peroxide solution|data/reagents/7722-84-1.json|
|7732-18-5|E-Toxate™ Water|data/reagents/7732-18-5.json|
|7758-11-4|Potassium phosphate dibasic, anhydrous,|data/reagents/7758-11-4.json|
|78-78-4|2-Methylbutane|data/reagents/78-78-4.json|
|78111-17-8|Okadaic acid|data/reagents/78111-17-8.json|
|79-06-1|Acrylamide|data/reagents/79-06-1.json|
|79-09-4|Propionic acid|data/reagents/79-09-4.json|
|79-31-2|Isobutyric acid|data/reagents/79-31-2.json|
|79902-63-9|Simvastatin|data/reagents/79902-63-9.json|
|8063-07-8|Kanamycin solution, from Streptomyces|data/reagents/8063-07-8.json|
|82410-32-0|Ganciclovir|data/reagents/82410-32-0.json|
|83-44-3|Deoxycholic acid|data/reagents/83-44-3.json|
|83-79-4|Rotenone|data/reagents/83-79-4.json|
|83730-53-4|L-Buthionine-sulfoximine|data/reagents/83730-53-4.json|
|84371-65-3|Mifepristone|data/reagents/84371-65-3.json|
|85622-93-1|Temozolomide|data/reagents/85622-93-1.json|
|856925-71-8|(−)-Blebbistatin|data/reagents/856925-71-8.json|
|85721-33-1|Ciprofloxacin|data/reagents/85721-33-1.json|
|866405-64-3|Dorsomorphin|data/reagents/866405-64-3.json|
|87-51-4|3-Indoleacetic acid|data/reagents/87-51-4.json|
|88-89-1|Picric acid|data/reagents/88-89-1.json|
|88321-09-9|E-64d|data/reagents/88321-09-9.json|
|92339-11-2|OptiPrep™ Density Gradient Medium|data/reagents/92339-11-2.json|
|93211-80-4|Giemsa Stain, Modified Solution|data/reagents/93211-80-4.json|
|934389-88-5|LY-294,002 hydrochloride|data/reagents/934389-88-5.json|
|93777-65-2|EGTA-Tris (Tris-EGTA)|data/reagents/93777-65-2.json|
|96-27-5|Thioglycerol|data/reagents/96-27-5.json|
|96-49-1|Ethylene carbonate|data/reagents/96-49-1.json|
|97-67-6|L-(−)-Malic acid|data/reagents/97-67-6.json|
|98-92-0|Nicotinamide|data/reagents/98-92-0.json|
|98849-88-8|FLAG® Peptide|data/reagents/98849-88-8.json|
|99-66-1|2-Propylpentanoic acid|data/reagents/99-66-1.json|

## Machine-Readable Detail

Full per-record results are in `audits/sds_identity_audit_2026-05-13.json`.
