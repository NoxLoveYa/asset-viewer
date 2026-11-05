-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Exports a model as a rig for the asset viewer's roblox-rig-export plugin (plugins/roblox-rig-export/README.md),
-- straight into it: every visible part with its real mesh (EditableMesh, fitted to the part's size as a MeshPart or a
-- classic head draws it), colour, texture or SurfaceAppearance maps and decals, the Shirt / Pants templates, and every
-- joint (Motor6D, AnimationConstraint, or a weld for a rigid part such as an accessory) as part1 -> parent, C0, C1.
-- Read-only in Studio. With the viewer running, run with execute_luau (Edit or Server) after setting NAME (the rig's
-- file name), MODEL (the Model) and ROOT (its root part's name); a held item (a prop) also sets GRIP (the character's
-- grip Attachment it joins, e.g. RightHand.RightGripAttachment; the item's HandGrip gives C1). MESHES (optional) is a
-- model whose same-named MeshParts lend their meshes where Studio may not read the model's own. MESH (optional) is a
-- prop's model file in the project (e.g. a held item's FBX): its MeshParts go as boxes and the viewer draws that file's
-- meshes and maps over them (a big model's meshes would pass Studio's 1 MB post limit). PORT (optional) is the viewer's
-- port when it is not 8765. It writes <rigsDir>/<NAME>.json and its images in <rigsDir>/<NAME>/ (an image Studio may not
-- read comes from Roblox's thumbnail). The injected globals: NAME, MODEL, ROOT, GRIP, MESHES, MESH, PORT.
local NAME: string = NAME
local MODEL: Model = MODEL
local ROOT: string = ROOT
local GRIP: Attachment? = GRIP
local MESHES: Model? = MESHES
local MESH: string? = MESH
local PORT: number = PORT or 8765
local URL = `http://127.0.0.1:{PORT}/api/plugins/roblox-rig-export/rig/{NAME}`

local AssetService = game:GetService("AssetService")
local EncodingService = game:GetService("EncodingService")
local HttpService = game:GetService("HttpService")

local warnings = {}
local function r(n: number): number
	return math.round(n * 10000) / 10000
end
local function cf(c: CFrame): { number }
	local t = { c:GetComponents() }
	for i, v in t do
		t[i] = r(v)
	end
	return t
end
-- an accessory's Handle goes by the accessory's name, so it never meets a held item's Handle joint in a clip
local function name(p: Instance): string
	return if p.Parent and p.Parent:IsA("Accessory") then p.Parent.Name else p.Name
end

-- An image asset as rigs/<NAME>/<id>.png, read in Studio or else Roblox's thumbnail of it; its path from rigs/.
local images = {}
local function image(content: string): string?
	local id = content:match("(%d+)%D*$")
	if not id then
		return nil
	end
	if images[id] == nil then
		local ok, img = pcall(AssetService.CreateEditableImageAsync, AssetService, Content.fromUri("rbxassetid://" .. id))
		local sent, err = pcall(function()
			if ok then
				local pixels = (img :: EditableImage):ReadPixelsBuffer(Vector2.zero, img.Size)
				local size, step = buffer.len(pixels), 700000 -- base64 of a chunk stays under Studio's 1 MB post limit
				local parts = math.ceil(size / step)
				for part = 0, parts - 1 do
					local chunk = buffer.create(math.min(step, size - part * step))
					buffer.copy(chunk, 0, pixels, part * step, buffer.len(chunk))
					local body = buffer.tostring(EncodingService:Base64Encode(chunk))
					local query = `w={img.Size.X}&h={img.Size.Y}&part={part}&parts={parts}`
					HttpService:PostAsync(`{URL}/{id}.png?{query}`, body, Enum.HttpContentType.TextPlain)
				end
				img:Destroy()
			else
				HttpService:PostAsync(`{URL}/{id}.png?thumb={id}`, "", Enum.HttpContentType.TextPlain)
			end
		end)
		images[id] = if sent then `{NAME}/{id}.png` else false
		if not sent then
			table.insert(warnings, `image {id} not saved: {err}`)
		end
	end
	return images[id] or nil
end

-- A mesh asset as flat arrays (positions in studs in the part's space, normals, Roblox UVs with v down, triangle
-- indices), fitted into size around the part's centre plus offset.
local function mesh(content: Content, size: Vector3, offset: Vector3?): {}?
	local ok, em = pcall(AssetService.CreateEditableMeshAsync, AssetService, content)
	if not ok then
		return nil
	end
	local lo, hi = Vector3.one * math.huge, -Vector3.one * math.huge
	for _, v in em:GetVertices() do
		local p = em:GetPosition(v)
		lo, hi = lo:Min(p), hi:Max(p)
	end
	local centre, scale = (lo + hi) / 2, size / (hi - lo):Max(Vector3.one * 1e-6)
	local out = { v = {}, n = {}, uv = {}, i = {} }
	local corners, count = {}, 0
	for _, f in em:GetFaces() do
		local verts, uvs, normals = em:GetFaceVertices(f), em:GetFaceUVs(f), em:GetFaceNormals(f)
		for k = 1, #verts do
			local key = `{verts[k]}/{uvs[k]}/{normals[k]}`
			local at = corners[key]
			if not at then
				local p = (em:GetPosition(verts[k]) - centre) * scale + (offset or Vector3.zero)
				local n = if normals[k] then em:GetNormal(normals[k]) else Vector3.zero
				local uv = if uvs[k] then em:GetUV(uvs[k]) else Vector2.zero
				table.insert(out.v, r(p.X))
				table.insert(out.v, r(p.Y))
				table.insert(out.v, r(p.Z))
				table.insert(out.n, r(n.X))
				table.insert(out.n, r(n.Y))
				table.insert(out.n, r(n.Z))
				table.insert(out.uv, r(uv.X))
				table.insert(out.uv, r(uv.Y))
				at = count
				count += 1
				corners[key] = at
			end
			table.insert(out.i, at)
		end
	end
	em:Destroy()
	return out
end

local root = MODEL:FindFirstChild(ROOT, true) :: BasePart
local rig = { root = ROOT, parts = {}, joints = {}, mesh = MESH }
for _, d in MODEL:GetDescendants() do
	if d:IsA("BasePart") and d.Transparency < 1 then
		local c = d.Color
		local part = {
			size = { r(d.Size.X), r(d.Size.Y), r(d.Size.Z) },
			color = { math.round(c.R * 255), math.round(c.G * 255), math.round(c.B * 255) },
			cf = cf(root.CFrame:ToObjectSpace(d.CFrame)),
		}
		local special = d:FindFirstChildWhichIsA("SpecialMesh")
		if d:IsA("MeshPart") and not MESH then
			part.mesh = mesh(Content.fromUri(d.MeshId), d.Size)
			local lender = MESHES and MESHES:FindFirstChild(d.Name)
			if not part.mesh and lender and lender:IsA("MeshPart") then
				part.mesh = mesh(Content.fromUri(lender.MeshId), d.Size)
				table.insert(warnings, `{d.Name}: mesh lent by {MESHES.Name}`)
			end
			if not part.mesh then
				table.insert(warnings, `{d.Name}: mesh not readable, drawn as a box`)
			end
			local sa = d:FindFirstChildOfClass("SurfaceAppearance")
			if sa then
				local ok, maps = pcall(function()
					return { Color = image(sa.ColorMap), Normal = image(sa.NormalMap), Metalness = image(sa.MetalnessMap), Roughness = image(sa.RoughnessMap) }
				end)
				part.maps = if ok then maps else nil
			elseif d.TextureID ~= "" then
				part.texture = image(d.TextureID)
			end
		elseif special and special.MeshType == Enum.MeshType.Head then
			-- the classic head mesh draws as wide as the part's narrow side, times the mesh's Scale
			local w = math.min(d.Size.X, d.Size.Z)
			part.mesh = mesh(Content.fromUri("rbxasset://fonts/head.mesh"), Vector3.new(w, d.Size.Y, w) * special.Scale, special.Offset)
		end
		local decals = {}
		for _, decal in d:GetChildren() do
			if decal:IsA("Decal") and decal.Texture ~= "" then
				table.insert(decals, { face = decal.Face.Name, texture = image(decal.Texture) })
			end
		end
		part.decals = if #decals > 0 then decals else nil
		rig.parts[name(d)] = part
	elseif d:IsA("Motor6D") and d.Part0 and d.Part1 then
		rig.joints[name(d.Part1)] = { parent = name(d.Part0), c0 = cf(d.C0), c1 = cf(d.C1) }
	elseif d:IsA("AnimationConstraint") and d.Attachment0 and d.Attachment1 then
		local a0, a1 = d.Attachment0 :: Attachment, d.Attachment1 :: Attachment
		rig.joints[name(a1.Parent :: Instance)] =
			{ parent = name(a0.Parent :: Instance), c0 = cf(a0.CFrame), c1 = cf(a1.CFrame) }
	end
end
-- parts welded rigidly ride the part they are welded to, at their rest offset
for _, d in MODEL:GetDescendants() do
	if (d:IsA("Weld") or d:IsA("WeldConstraint")) and d.Part0 and d.Part1 then
		local child, parent = d.Part0 :: BasePart, d.Part1 :: BasePart
		if rig.joints[name(child)] or child == root then
			child, parent = parent, child
		end
		if rig.parts[name(child)] and not rig.joints[name(child)] then
			rig.joints[name(child)] =
				{ parent = name(parent), c0 = cf(parent.CFrame:ToObjectSpace(child.CFrame)), c1 = cf(CFrame.identity) }
		end
	end
end
local shirt, pants = MODEL:FindFirstChildOfClass("Shirt"), MODEL:FindFirstChildOfClass("Pants")
if shirt or pants then
	rig.clothing = {
		shirt = shirt and shirt.ShirtTemplate ~= "" and image(shirt.ShirtTemplate) or nil,
		pants = pants and pants.PantsTemplate ~= "" and image(pants.PantsTemplate) or nil,
	}
end
if GRIP then
	local handGrip = root:FindFirstChild("HandGrip") :: Attachment?
	rig.grip = {
		part = (GRIP.Parent :: Instance).Name,
		c0 = cf(GRIP.CFrame),
		c1 = cf(if handGrip then handGrip.CFrame else CFrame.identity),
	}
end
local reply = HttpService:PostAsync(URL, HttpService:JSONEncode(rig), Enum.HttpContentType.ApplicationJson)
return reply .. (if #warnings > 0 then "\nwarnings: " .. table.concat(warnings, "; ") else "")
