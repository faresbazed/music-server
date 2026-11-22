#import "Library.h"

NSString *const LibraryDidChange = @"OfflineMusicLibraryDidChange";

NSString *Text(id value, NSString *fallback) {
    return [value isKindOfClass:NSString.class] && [value length] ? value : fallback;
}

NSString *MatchedID(NSDictionary *track) {
    NSString *tid = Text([track objectForKey:@"id"], nil);
    id flag = [track objectForKey:@"has_file"];
    if (![flag isKindOfClass:NSNumber.class] || ![flag boolValue] || !tid.length || tid.length > 128) return nil;
    NSCharacterSet *invalid = [[NSCharacterSet characterSetWithCharactersInString:
        @"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"] invertedSet];
    return [tid rangeOfCharacterFromSet:invalid].location == NSNotFound ? tid : nil;
}

NSString *AudioExtension(id value) {
    NSString *ext = [Text(value, @"") lowercaseString];
    return [@[@"mp3", @"m4a", @"aac", @"wav", @"aif", @"aiff", @"flac", @"ogg"] containsObject:ext] ? ext : nil;
}

static NSString *Key(NSString *origin, NSString *tid) {
    return [NSString stringWithFormat:@"%@/%@", origin, tid];
}

static BOOL Fail(NSError **error, NSString *message) {
    if (error) *error = [NSError errorWithDomain:@"OfflineMusic.Library" code:1
        userInfo:@{NSLocalizedDescriptionKey:message}];
    return NO;
}

@implementation Library
- (instancetype)initWithDirectory:(NSURL *)directory {
    if ((self = [super init])) {
        _root = [directory copy];
        _byKey = [NSMutableDictionary new];
    }
    return self;
}

- (BOOL)reload:(NSError **)error {
    NSFileManager *fm = [NSFileManager defaultManager];
    if (![fm createDirectoryAtURL:_root withIntermediateDirectories:YES attributes:nil error:error]) return NO;
    NSArray *folders = [fm contentsOfDirectoryAtPath:_root.path error:error];
    if (!folders) return NO;
    NSMutableDictionary *fresh = [NSMutableDictionary dictionary];
    for (NSString *name in folders) {
        if (![name.pathExtension isEqualToString:@"track"]) continue;
        NSURL *folder = [_root URLByAppendingPathComponent:name isDirectory:YES];
        NSDictionary *rec = [NSDictionary dictionaryWithContentsOfURL:[folder URLByAppendingPathComponent:@"Info.plist"]];
        NSString *origin = Text([rec objectForKey:@"origin"], nil);
        NSString *tid = MatchedID(rec);
        if (!origin || !tid || !AudioExtension([rec objectForKey:@"ext"]) ||
            ![[folder lastPathComponent] isEqualToString:[rec objectForKey:@"folder"]] ||
            ![fm fileExistsAtPath:[[self audioURL:rec] path]]) continue;
        [fresh setObject:rec forKey:Key(origin, tid)];
    }
    [_byKey setDictionary:fresh];
    return YES;
}

- (NSArray *)records {
    NSSortDescriptor *title = [NSSortDescriptor sortDescriptorWithKey:@"title" ascending:YES
        selector:@selector(localizedCaseInsensitiveCompare:)];
    return [[_byKey allValues] sortedArrayUsingDescriptors:@[title]];
}

- (NSURL *)audioURL:(NSDictionary *)record {
    NSURL *folder = [_root URLByAppendingPathComponent:[record objectForKey:@"folder"] isDirectory:YES];
    return [folder URLByAppendingPathComponent:[@"Audio." stringByAppendingString:[record objectForKey:@"ext"]]];
}

- (BOOL)containsTrack:(NSDictionary *)track origin:(NSString *)origin {
    return [_byKey objectForKey:Key(origin, MatchedID(track))] != nil;
}

- (BOOL)saveFile:(NSURL *)temporary track:(NSDictionary *)track origin:(NSString *)origin error:(NSError **)error {
    NSString *ext = AudioExtension([track objectForKey:@"ext"]);
    if (!origin.length || !MatchedID(track) || !ext) return Fail(error, @"Invalid matched track or unsupported file type.");
    if ([self containsTrack:track origin:origin]) return YES;
    NSFileManager *fm = [NSFileManager defaultManager];
    NSDictionary *attrs = [fm attributesOfItemAtPath:temporary.path error:error];
    if (!attrs) return NO;
    if (![attrs fileSize]) return Fail(error, @"The server returned an empty file.");
    NSString *uuid = [[NSUUID UUID] UUIDString];
    NSString *name = [uuid stringByAppendingPathExtension:@"track"];
    // Publish the audio and its index entry together, never a half-saved track.
    NSURL *stage = [[NSURL fileURLWithPath:NSTemporaryDirectory() isDirectory:YES] URLByAppendingPathComponent:uuid];
    NSURL *folder = [_root URLByAppendingPathComponent:name isDirectory:YES];
    NSDictionary *rec = @{@"folder":name, @"origin":origin, @"ext":ext, @"id":MatchedID(track), @"has_file":@1,
        @"title":Text([track objectForKey:@"title"], @"Untitled track"),
        @"artist":Text([track objectForKey:@"artist"], @"Unknown artist")};
    if (![fm createDirectoryAtURL:stage withIntermediateDirectories:NO attributes:nil error:error]) return NO;
    BOOL ok = [fm moveItemAtURL:temporary toURL:[stage URLByAppendingPathComponent:[@"Audio." stringByAppendingString:ext]] error:error];
    if (ok && ![rec writeToURL:[stage URLByAppendingPathComponent:@"Info.plist"] atomically:YES])
        ok = Fail(error, @"Could not write the offline index. Check available storage.");
    if (ok) ok = [fm moveItemAtURL:stage toURL:folder error:error];
    if (!ok) { [fm removeItemAtURL:stage error:NULL]; return NO; }
    [_byKey setObject:rec forKey:Key(origin, MatchedID(track))];
    [[NSNotificationCenter defaultCenter] postNotificationName:LibraryDidChange object:self];
    return YES;
}

- (BOOL)removeRecord:(NSDictionary *)record error:(NSError **)error {
    NSString *key = Key([record objectForKey:@"origin"], MatchedID(record));
    if (![_byKey objectForKey:key]) return Fail(error, @"This track is not on this iPad.");
    NSURL *folder = [[self audioURL:[_byKey objectForKey:key]] URLByDeletingLastPathComponent];
    if (![[NSFileManager defaultManager] removeItemAtURL:folder error:error]) return NO;
    [_byKey removeObjectForKey:key];
    [[NSNotificationCenter defaultCenter] postNotificationName:LibraryDidChange object:self];
    return YES;
}
@end
