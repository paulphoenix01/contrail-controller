#!/build/anantha/thirdparty/ruby/bin/ruby -W0

require 'pp'
require 'rails'

def init_globals
    @debug = false
    @db = Hash.new
    @events = [ ]
    @seen = Hash.new(false)
end

def get_uuid (u)
    c = sprintf("%032x",
                (u["uuid_mslong"].to_i << 64) | u["uuid_lslong"].to_i).split(//)
    c.insert(8, "-").insert(13, "-").insert(18, "-").insert(23, "-").join
end

def read_xml_to_json (file_name)
    ifd = file_name.nil? ? STDIN : File.open(file_name, "r")
    xml = ifd.read() # File.read("server_parser_test6.xml")
    ifd.close

    # Add seq-numbers to resultItems to note down the order..
    seq = 0
    x = xml.split(/(<)/).collect { |token|
        token =~ /^resultItem(>.*)/ ? "resultItem _seq=\"#{seq+=1}\"#{$1}":token
    }
    xml = x.join
    puts xml if @debug
    json = JSON.pretty_generate(Hash.from_xml(xml))
    return JSON.parse(json)
end

def read_items (json)
# if jo["Envelope"]["Body"]["response"]["pollResult"]["updateResult"].nil?
# jo["Envelope"]["Body"]["response"]["pollResult"]["searchResult"]["resultItem"]
    t = json["Envelope"]["Body"]["response"]["pollResult"]
    records = [ ]
    t.each { |type, tmp|
        r = [ ]
        f = tmp.kind_of?(Array) ? tmp : [tmp["resultItem"]]
        f.each { |i|
            if (i.class == Array and i[0].key? "_seq") or
               (i.class == Hash and i.key? "_seq")
                r.push i
                next
            end
            r += i["resultItem"].kind_of?(Array) ? i["resultItem"] :
                                                   [i["resultItem"]]
        }
        r.flatten!
        r.each { |i| i["_oper"] = type }
        records += r
    }

    records.sort! { |k1, k2| k1["_seq"] <=> k2["_seq"] }
    pp records if @debug
    init_fake_db(records)
    return records
end

def init_fake_db (records)
    records.each { |record|
        next if !record["metadata"].key? "id_perms" or
                !record["metadata"]["id_perms"].key? "uuid"
        uuid = get_uuid(record["metadata"]["id_perms"]["uuid"])
        next if @db.key? uuid
        next if record["identity"]["name"] !~ /contrail:(.*?):(.*$)/
        fq_name = $2; type = $1.gsub("-", "_")
        @db[uuid] = {
            "uuid" => uuid, "fq_name" => fq_name.split(/:/),
            "type" => type
        }
    }
    pp @db if @debug
end

def from_name (fq)
    @db.each { |k, v| return v if v["fq_name"] == fq.split(/:/) }
    return nil
end

def oper_convert (oper)
    return "CREATE" if oper == "createResult"
    return "UPDATE" if oper == "updateResult"
    return "DELETE" if oper == "deleteResult"
    return nil
end

def print_db (oper, uuid, fq_name, type)
    if oper == "updateResult"
        oper = "createResult" if !@seen[uuid]
        @seen[uuid] = true
    end

    t = type
    t = $1 if type =~ /\"(.*)\"/
    if fq_name.kind_of?(Array)
        fqs = fq_name.join(":")
    else
        fqs = fq_name
        if fq_name.start_with? "["
            fqs = JSON.parse(fq_name).join(":")
        end
    end

    event = {
        "oper" => oper_convert(oper), "fq_name" => fq_name, "type" => type,
        "uuid" => "#{@events.size}:#{uuid}",
        "imid" => "contrail:#{t}:#{fqs}",
    }
    @events.push({
                      "operation" => "rabbit_enqueue",
                      "message" => event.to_json,
                      "db" => @db.deep_dup }
                )
end

def extract_fq_name_and_type (name)
    return $2, $1.gsub("-", "_") if name =~ /contrail:(.*?):(.*$)/
end

def parse_links (record)
    fq1, t1 = extract_fq_name_and_type(record["identity"][0]["name"])
    fq2, t2 = extract_fq_name_and_type(record["identity"][1]["name"])

#   return if record["identity"][0]["name"] !~ /contrail:(.*?):(.*$)/
#   fq1 = $2; t1 = $1.gsub("-", "_")
#   return if record["identity"][1]["name"] !~ /contrail:(.*?):(.*$)/
#   fq2 = $2; t2 = $1.gsub("-", "_")

    # Add/Remove ref and back-ref
    r1 = from_name(fq1)
    r2 = from_name(fq2)

    k1 = "ref:" + t2 + ":" + r2["uuid"] if !r2.nil?
    k2 = "backref:" + t1 + ":" + r1["uuid"] if !r1.nil?
    if record["_oper"] == "updateResult"
        r1[k1] = nil if !r1.nil?; r2[k2] = nil if !r2.nil?
    else
        r1.delete k1 if !r1.nil?; r2.delete k2 if !r2.nil?;
    end

    # Treat link deletes also as updates (to db)
    print_db("updateResult", r1["uuid"], r1["fq_name"], r1["type"]) if !r1.nil?
end

def parse_nodes (record)
    return if !record.key? "identity" or !record["identity"].key? "name"
    return if record["identity"]["name"] !~ /contrail:(.*?):(.*$)/
    fq_name = $2; type = $1.gsub("-", "_")
    obj = from_name(fq_name)
    return if obj.nil?
    record["metadata"].each { |k, v| obj["prop:" + k] = v }

    # Remove ifmap stuff from id-perms
    if obj.key? "prop:id_perms"
        n = Hash.new
        obj["prop:id_perms"].each { |k, v| n[k] = v if k !~ /^ifmap_|xmlns:/ }
        obj["prop:id_perms"] = n
    end

    # Convert prop to json string
    n = Hash.new
    uuid = obj["uuid"]
    obj.each {|k, v|
        next if k == "uuid"
        n[k] = v.class == String ? "\"#{v}\"" : "#{v.to_json}"
    }
    @db[uuid] = n
    print_db(record["_oper"], obj["uuid"], obj["fq_name"], obj["type"])
end

def delete_nodes (record)
    uuid = get_uuid(record["metadata"]["id_perms"]["uuid"])
    if !uuid.nil?
        if @db.key? uuid and @db[uuid].key? "fq_name"
            fq_name = @db[uuid]["fq_name"]
            type = @db[uuid]["type"]
        else
            fq_name, type = extract_fq_name_and_type(record["identity"]["name"])
        end
    else
        fq_name = nil
        type = nil
    end
    @db.delete uuid
    print_db(record["_oper"], uuid, fq_name, type)
end

def process (records)
    records.each { |record|
        if record["identity"].kind_of?(Array) and record["identity"].size == 2
            parse_links(record)
        elsif record["_oper"] == "deleteResult"
            delete_nodes(record)
        else
            parse_nodes(record)
        end
    }
end

def extract_test_to_xml_list (cc_file)
    fd = File.open(cc_file, "r")
    xml_files = [ ]
    fd.read.split(/\n/).each { |line|
        if line =~ /^TEST_F\(/
            xml_files.push([ ])
            next
        end
        if line =~ /FileRead\("(.*)"\)/
            xml_files.last.push $1
        end
    }
    fd.close
    return xml_files
end

def process_files (files)
    return if files.empty?
    init_globals
    pp files if @debug
    files.each { |file_name|
        @events.push({"operation" => "pause"}) if !@events.empty?
        puts file_name
        json = read_xml_to_json(file_name)
        records = read_items(json)
        init_fake_db(records)
        process(records) # Process updates
    }

    json_file = File.dirname(files[0])+"/"+File.basename(files[0],".*")+".json"
    puts JSON.pretty_generate(@events) if @debug
    puts "Produced #{json_file}"
    File.open(json_file, "w") { |fp| fp.puts JSON.pretty_generate(@events) }
end

def main
    # for cc file, extract xml files from the ut cc file.
    xml_files = [ ]
    ARGV.each { |file_name|
        if file_name.end_with? ".cc" then
            extract_test_to_xml_list(file_name).each { |fl| process_files(fl) }
        else
            xml_files << file_name
        end
    }
    process_files(xml_files)
end

main
