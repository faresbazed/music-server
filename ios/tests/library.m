#import "Library.h"

#define CHECK(expr) do { if (!(expr)) { fprintf(stderr, "FAIL at line %d: %s\n", __LINE__, #expr); return 1; } } while (0)

int main(int argc, char **argv) {
    @autoreleasepool {
        CHECK(argc == 2);
        NSURL *base = [NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[1]] isDirectory:YES];
        NSURL *root = [base URLByAppendingPathComponent:@"downloads" isDirectory:YES];
        NSURL *file = [base URLByAppendingPathComponent:@"temporary.mp3"];
        Library *lib = [[Library alloc] initWithDirectory:root];
        NSString *alpha = @"https://alpha.example", *beta = @"https://beta.example";
        NSDictionary *match = @{@"id":@"abc123", @"ext":@"mp3", @"title":@"Example", @"artist":@"Artist", @"has_file":@1};
        NSError *error = nil;
        NSData *data = [@"sample file bytes" dataUsingEncoding:NSUTF8StringEncoding];
        CHECK([lib reload:&error]);
        CHECK([lib records].count == 0);
        CHECK(!MatchedID((@{@"id":[NSNull null], @"has_file":@0})));
        CHECK(!MatchedID((@{@"id":@"../invalid", @"has_file":@1})));
        CHECK(!AudioExtension(@"bad/mp3"));
        CHECK([[NSData data] writeToURL:file atomically:YES]);
        CHECK(![lib saveFile:file track:match origin:alpha error:&error]);
        CHECK([lib records].count == 0);
        CHECK([data writeToURL:file atomically:YES]);
        CHECK([lib saveFile:file track:match origin:alpha error:&error]);
        CHECK([lib containsTrack:match origin:alpha]);
        CHECK(![lib containsTrack:match origin:beta]);
        CHECK([lib saveFile:file track:match origin:alpha error:&error]);
        CHECK([lib records].count == 1);
        CHECK([data writeToURL:file atomically:YES]);
        CHECK([lib saveFile:file track:match origin:beta error:&error]);
        CHECK([lib records].count == 2);
        // Interrupted/invalid entries must not appear as completed downloads on relaunch.
        NSURL *incomplete = [root URLByAppendingPathComponent:@"partial.track" isDirectory:YES];
        CHECK([[NSFileManager defaultManager] createDirectoryAtURL:incomplete
            withIntermediateDirectories:YES attributes:nil error:&error]);
        NSMutableDictionary *orphan = [match mutableCopy];
        [orphan setObject:@"partial.track" forKey:@"folder"];
        [orphan setObject:@"https://partial.example" forKey:@"origin"];
        CHECK([orphan writeToURL:[incomplete URLByAppendingPathComponent:@"Info.plist"] atomically:YES]);
        Library *reopened = [[Library alloc] initWithDirectory:root];
        CHECK([reopened reload:&error]);
        CHECK([reopened records].count == 2);
        NSDictionary *saved = [[reopened records] firstObject];
        CHECK([[[NSData alloc] initWithContentsOfURL:[reopened audioURL:saved]] isEqual:data]);
        CHECK([reopened removeRecord:saved error:&error]);
        CHECK([lib reload:&error]);
        CHECK([lib records].count == 1);
        puts("PASS: disk index, bytes, empty/invalid input, deduplication, server isolation, restart and removal");
        return 0;
    }
}
